# -*- coding: utf-8 -*-
import asyncio
import inspect
import json
import logging
import threading
import uuid

from odoo.exceptions import AccessDenied
from odoo import _, api, models, fields
from odoo.tools import date_utils
from xmppgcm import GCM, XMPPEvent

logger = logging.getLogger(__name__)

def cursored(func):
    ''' Creates a new cursor and self, executes function and then commits and 
        closes the cursor'''
    def inner(self,*args,**kwargs):
        new_cr = self.pool.cursor()
        self = self.with_env(self.env(cr=new_cr))
        ret = func(self,*args,**kwargs)
        new_cr.commit()
        new_cr.close()
        return ret
    return inner
        
class FirebaseBridge(models.Model):
    ''' Google FCM Bridge to Odoo API, using XMPP'''
    _name = 'firebase.bridge'
    _description = 'Firebase Bridge'
    
    name = fields.Char(
        string=_('Name'),
        required=True,
        index=True,
        copy=False,
    )
    server = fields.Char(_('Firebase server'), default='fcm-xmpp.googleapis.com')
    port = fields.Integer(_('Port'), default = 5235)
    server_id = fields.Char(_('Server ID'))
    server_key = fields.Char(_('Server Key'))
    server_domain = fields.Char('Firebase domain', default= 'fcm.googleapis.com')
    use_ssl = fields.Boolean(_('Use SSL'), default=True)
    connected = fields.Boolean(_('Connected'), default=False)
    session_ids = fields.One2many(comodel_name='firebase.session',inverse_name='bridge_id', string='Sessions')
    
    def connect(self):
        logger.debug("Fireserver %s connecting" % self.id)
        #self.write({'connected': False})
        self.connected = False
        thread_name = 'firebase-%s' % self.id
        thread = threading.Thread(name=thread_name,target=self._run_thread, args=(self.id,))
        thread.firebase_queue = []
        thread.start()
    
    def get_thread(self):
        ''' Gets the thread running the XMPP connection, based on the id'''
        thread_name = 'firebase-%s' % self.id
        for t in threading.enumerate():
            if t.name == thread_name:
                return t
        
    def disconnect(self):
        t = self.get_thread()
        if t:
            t._fstopped = True
        
    @cursored
    def _run_thread(self,server_id):
        logger.info("Starting Firebase thread %s" % server_id)
        t = threading.currentThread()
        t._fstopped = False
        t._attempts = 0
        t.server = self.server
        t.port = self.port
        t.use_ssl = self.use_ssl
        
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            # slixmpp can not handle not having an event_loop
            # see: https://lab.louiz.org/poezio/slixmpp/-/issues/3456
            # This is a work-around to this problem
            asyncio.set_event_loop(asyncio.new_event_loop())
        
        xmpp = GCM('%s@%s' % (self.server_id, self.server_domain), self.server_key)
        t.xmpp = xmpp
        xmpp.default_port = self.port

        xmpp.add_event_handler(XMPPEvent.CONNECTED, self.on_connected)
        xmpp.add_event_handler(XMPPEvent.DISCONNECTED, self.on_disconnected)
        xmpp.add_event_handler(XMPPEvent.RECEIPT, self.on_receipt)
        xmpp.add_event_handler(XMPPEvent.MESSAGE, self.on_message)
        xmpp.connect((t.server, t.port), use_ssl=t.use_ssl) 
        
        while not threading.currentThread()._fstopped:
            xmpp.process(forever=True, timeout=5)
            firebase_queue = threading.currentThread().firebase_queue
            while len(firebase_queue) > 0:
                message = firebase_queue.pop()
                to = message.pop('to',None)
                if to:
                    logger.info('Sending to %s' % to)
                    xmpp.send_gcm(to,message)

        xmpp.disconnect(0.0)
        logger.warning('Firebase Bridge %s exiting' % server_id)
        

    @cursored
    def on_connected(self,queue_length):
        logging.info('Firebase Bridge %s connected' % self.name)
        self.get_thread()._attempts = 0 # Reset connection attempts
        self.connected = True
        
    @cursored
    def on_disconnected(self, draining):
        logging.info('Firebase Bridge %s disconnected' % self.name)
        self.connected = False
        t = self.get_thread()
        if t._attempts < 5:
            t._attempts = t._attempts +1
            logging.info('Firebase Bridge %s reconnecting. attempt #%s' % (self.id, t.attempts))
            t.xmpp.connect((self.server, self.port), use_ssl=self.use_ssl) 

    @cursored
    def on_receipt(self,data):
        logging.debug('Firebase Bridge %s receipt: %s' % (self.name, data))
        
    @cursored
    def on_message(self,message):
        logging.debug('Firebase Bridge %s received: %s' % (self.name, message.data))
        data = message.data.get('data')
        type = data.pop('type',None)
        if not type:
            return
        if type == 'login':
            self.authenticate(message)
        else:
            # TODO: extract key authentication function
            device = message.data.get('from')
            key = message.data.get('data').pop('key',None)
            session = self._get_session(device,key)
            if (session):
                session.last = fields.Datetime.now()
                message.data['key'] = key
                message.data['user_id'] = session.user_id.id
                if type == 'rpc':
                    self.do_rpc(message)
            else:
                logger.warning('Unauthorized access. device:%s, key:%s, data:%s' % (device,key,message.data.get('data')))
            
    def _get_session(self,device,key):
        FirebaseSession = self.env['firebase.session']
        session_id = FirebaseSession.search(['&',('device','=',device),('key','=',key)])
        if session_id:
            return FirebaseSession.browse(int(session_id[0]))

            
    def do_rpc(self,message):
        ''' Make API call.
            Response will be sent in FCM messages to device.
            TODO: add option to NOT send response.
        '''
        data = message.data.get('data')
        model = data.get('model')
        method = data.get('method')
        fn_args = json.loads(data.get('args','[]'))
        fn_kwargs = json.loads(data.get('kwargs','{}'))
        user_id = message.data.get('user_id')
        
        logger.debug('do_rpc (uid:%s): %s,%s,%s,%s' % (user_id,model,method, fn_args,fn_kwargs))
        
        obj = self.env[model].with_user(user_id)
        fn = getattr(obj,method)
        
        ret = fn(*fn_args,**fn_kwargs)
        
        # Normalize return type
        if isinstance(ret,str):
            ret = json.loads(ret)
        elif inspect.isclass(ret):
            ret = ret.read()
        elif isinstance(ret,models.Model):
            ret = ret.read()
        #print('do_rpc ret:',len(ret), type(ret),ret)
        
        if ret:
            for obj in ret:
                if isinstance(obj,models.Model):
                    obj = obj.read()[0]
                msg = {
                    'to': message.data.get('from'),
                    'type': 'object',
                    'model': model,
                    'data': json.dumps(obj, default=date_utils.json_default)
                }
                self.send_message(msg)
                
                    
    def authenticate(self,message):
        logging.debug('Firebase Bridge %s authenticating: %s' % (self, message.data.get('data')))
        data = message.data.get('data')
        dbname = self.env.cr.dbname
        logging.debug('Firebase Bridge calling authenticate %s,%s' % (dbname,data.get('username')))
        try:
            uid =  self.env['res.users'].authenticate(
                dbname,
                data.get('username'), 
                data.get('password'),
                {'interactive':False}
            )
            user = self.env['res.users'].browse(uid)
            if (uid):
                device= message.data.get('from')
                logging.info('Firebase Bridge new session %s,%s' % (user.name, device))
                #first close all sessions from same device
                FirebaseSession = self.env['firebase.session']
                FirebaseSession.search([('device','=',device)]).write({'closed':True})
                
                # Create new session
                values = {
                    'bridge_id': self.id,
                    'device': device,
                    'user_id' : uid,
                    'partner_id': user.partner_id.id,
                    'key':str(uuid.uuid4())[:8],
                    'last': fields.Datetime.now(),
                    'closed': False
                }
                session = FirebaseSession.create(values)
                self.env.cr.commit()
                data = {
                    'key': session.key,
                    'uid' : uid,
                    'name': user.name,
                    'partner_id': user.partner_id.id
                }
                message = {
                    'to': session.device,
                    'type': 'login-ack',
                    'data': json.dumps(data, default=date_utils.json_default)
                }
                self.send_message(message)
        except AccessDenied:
            logging.warn('Firebase Bridge login denied %s@%s' % (data.get('username'), message.data.get('from')))
            message = {
                'to': message.data.get('from'),
                'type': 'login-nack',
                'data': '{}'
            }
            self.send_message(message)

    def send_to_partner(self,partner_id,model,obj):
        ''' Sends a message to all active sessions related to partner'''
        logger.info('send_to_partner %s, %s, %s ' % (partner_id,model,obj))
        if not isinstance(obj,str):
            obj = json.dumps(obj, default=date_utils.json_default)
            
        msg = {
            'type': 'object',
            'model': model,
            'data': obj
            }
        
        sessions = self.session_ids.filtered(lambda x: x.partner_id.id == partner_id and x.active == True)
        logger.debug('send_to_partner sessions: %s' % sessions)
        
        for session in sessions:
            msg['to']=session.device
            self.send_message(msg)
                
    def send_message(self,message):
        t = self.get_thread()
        if t:
            t.firebase_queue.append(message)
        else:
            logger.warning('send_message: Firebase Bridge thread for %s not found' % self.name)
