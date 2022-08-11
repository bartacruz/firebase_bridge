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
    session_timeout = fields.Integer(_('Session Timeout'),default=600)
    
    def connect(self):
        logger.debug("Fireserver %s connecting" % self.id)
        #self.write({'connected': False})
        self.connected = False
        thread_name = 'firebase-%s' % self.id
        thread = threading.Thread(name=thread_name,target=self._run_thread, args=(self.id, self.server, self.port, self.use_ssl,self.server_id, self.server_domain,self.server_key))        
        thread.start()
    
    def get_thread(self):
        ''' Gets the thread running the XMPP connection, based on the id'''
        thread_name = 'firebase-%s' % self.id
        for t in threading.enumerate():
            if t.name == thread_name:
                return t
    
    def _get_messages(self):
        return self.env['firebase.message'].search([('sent','=',None)])
        
    def disconnect(self):
        t = self.get_thread()
        if t:
            t._fstopped = True

    @cursored
    def message_loop(self, xmpp):
        logger.info("[Firebase Bridge] Checking messages")
        for message in self._get_messages():
            rtt = fields.Datetime.now()
            msg = {
                'type': message.type,
                'model': message.model,
                'data': message.data
            }
            devices = self._get_partner_devices(message)
            for device in devices:
                xmpp.send_gcm(device,msg)
                logger.info('[Firebase Bridge] Message %s sent to %s in %s',message.name, message.partner_id, (fields.Datetime.now() - rtt).total_seconds() )
            message.sent = fields.Datetime.now()

    
    def _run_thread(self,bridge_id,server,port,use_ssl,server_id,server_domain,server_key):
        
        logger.info("Starting Firebase thread %s: %s,%s,%s,%s,%s,%s", bridge_id,server,port,use_ssl,server_id,server_domain,server_key)
        t = threading.currentThread()
        t._fstopped = False
        t._attempts = 0
        t.server = server
        t.port = port
        t.use_ssl = use_ssl
        
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            # slixmpp can not handle not having an event_loop
            # see: https://lab.louiz.org/poezio/slixmpp/-/issues/3456
            # This is a work-around to this problem
            asyncio.set_event_loop(asyncio.new_event_loop())
        
        xmpp = GCM('%s@%s' % (server_id, server_domain), server_key)
        t.xmpp = xmpp
        xmpp.default_port = port

        xmpp.add_event_handler(XMPPEvent.CONNECTED, self.on_connected)
        xmpp.add_event_handler(XMPPEvent.DISCONNECTED, self.on_disconnected)
        xmpp.add_event_handler(XMPPEvent.RECEIPT, self.on_receipt)
        xmpp.add_event_handler(XMPPEvent.MESSAGE, self.on_message)
        xmpp.connect((server, port), use_ssl=use_ssl) 
        
        while not threading.currentThread()._fstopped:
            xmpp.process(forever=True, timeout=5)
            self.message_loop(xmpp)          

        xmpp.disconnect(0.0)
        logger.warning('Firebase Bridge %s exiting' % bridge_id)
        

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
        if t._attempts < 3:
            t._attempts = t._attempts +1
            logging.info('Firebase Bridge %s reconnecting. attempt #%s' % (self.id, t._attempts))
            t.xmpp.connect((self.server, self.port), use_ssl=self.use_ssl) 

    @cursored
    def on_receipt(self,data):
        logging.debug('Firebase Bridge %s receipt: %s' % (self.name, data))
        
    @cursored
    def on_message(self,message):
        logging.debug('Firebase Bridge %s received: %s' % (self.name, message.data))
        data = message.data.get('data')
        msg_type = data.pop('type',None)
        if not msg_type:
            return
        if msg_type == 'login':
            self.authenticate(message)
        else:
            # TODO: extract key authentication function
            device = message.data.get('from')
            key = message.data.get('data').pop('key',None)
            session = self._get_session(device,key)
            if session:
                session.last = fields.Datetime.now()
                message.data['key'] = key
                message.data['user_id'] = session.user_id.id
                if msg_type == 'rpc':
                    self.do_rpc(message)
            else:
                logger.warning('Unauthorized access. device:%s, key:%s, data:%s' % (device,key,message.data.get('data')))

    def _get_partner_devices(self,message):
        if message.device:
            return [message.device]
        sessions = self.session_ids.filtered(lambda x: x.partner_id.id == message.partner_id.id and x.active == True)
        logger.info('_get_partner_devices sessions: %s' % sessions)
        return [s.device for s in sessions]
    
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
        split_method = method.split('-nr')
        method = split_method[0]
        no_return = len(split_method) > 1
        fn_args = json.loads(data.get('args','[]'))
        fn_kwargs = json.loads(data.get('kwargs','{}'))
        user_id = message.data.get('user_id')
        
        logger.debug('do_rpc (uid:%s): %s,%s,%s,%s' % (user_id,model,method, fn_args,fn_kwargs))
        
        obj = self.env[model].with_user(user_id)
        fn = getattr(obj,method)
        
        ret = fn(*fn_args,**fn_kwargs)
        
        # Normalize return type
        if no_return or not ret or isinstance(ret,bool):
            return
        if isinstance(ret,str):
            ret = json.loads(ret)
        elif inspect.isclass(ret):
            ret = ret.read()
        elif isinstance(ret,models.Model):
            ret = ret.read()
        
        print('do_rpc ret:', type(ret),ret)
        
        if ret:
            for obj in ret:
                if isinstance(obj,models.Model):
                    obj = obj.read()[0]
                msg = {
                    'bridge_id': self.id,
                    'device': message.data.get('from'),
                    'type': 'object',
                    'model': model,
                    'data': json.dumps(obj, default=date_utils.json_default)
                }
                self.create_message(msg)

    def create_message(self, vals):
        msg = self.env['firebase.message'].create(vals)
        logger.info('%s: created firebase message %s for %s (type:%s, model:%s)',self._name,msg.name,msg.partner_id,msg.type,msg.model)
            
    def _oauth_authenticate(self,data):
        userid = self.env['res.users'].search(['&',['login','=',data.get('username')],['active','=',True]])
        if not userid:
            return False
        user = self.env['res.users'].browse(userid[0].id)
        print('_oauth_authenticate',userid,user,data)
        try:
            validation = self.env['res.users']._auth_oauth_validate(user.oauth_provider_id.id,data.get('password'))
            print('_oauth_authenticate validation:',validation)
            if validation['user_id'] == user.oauth_uid:
                return userid[0].id
        except:
            pass
        return False
        
                    
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
        except AccessDenied:
            logging.debug('Firebase Bridge calling oauth %s,%s' % (dbname,data.get('username')))
            oauth_uid = self._oauth_authenticate(data)
            if oauth_uid:
                uid = oauth_uid;
            else:
                logging.warning('Firebase Bridge login denied %s@%s' % (data.get('username'), message.data.get('from')))
                message = {
                    'bridge_id': self.id,
                    'device': message.data.get('from'),
                    'type': 'login-nack',
                    'data': '{}'
                }
                self.create_message(message)
                return
            
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
                'device': session.device,
                'type': 'login-ack',
                'partner_id': user.partner_id.id,
                'data': json.dumps(data, default=date_utils.json_default)
            }
            self.create_message(message)
        

    def send_to_partner(self,partner_id,model,obj):
        ''' Sends a message to all active sessions related to partner'''
        logger.info('send_to_partner %s, %s, %s ' % (partner_id,model,obj))
        if not isinstance(obj,str):
            obj = json.dumps(obj, default=date_utils.json_default)
            
        msg = {
            'bridge_id': self.id,
            'type': 'object',
            'model': model,
            'data': obj,
            'partner_id': partner_id,
            }
        sessions = self.session_ids.filtered(lambda x: x.partner_id.id == partner_id and x.active == True)
        for session in sessions:
            logger.debug('send_to_partner device: %s' % session.device)
            msg['device']=session.device
            self.create_message(msg)
    
    @api.model
    def clean_start(self):
        self.env['firebase.bridge'].search([]).write({'connected':False})
    
    def check_sessions(self):
        for record in self:
            record.session_ids._compute_active()
        return True
    
    def ping_sessions(self):
        for record in self:
            for s in record.session_ids.filtered(lambda x: x.active):
                print("_ping_sessions",(fields.Datetime.now() - s.last).total_seconds(),record.session_timeout/2)
                if (fields.Datetime.now() - s.last).total_seconds() > record.session_timeout/2:
                    s.ping()
        return True


