from odoo import _, api, models, fields
import logging
_logger = logging.getLogger(__name__)

class FirebaseSession(models.Model):
    _name = 'firebase.session'
    _description = 'Firebase Bridge device session'
    _rec_name = 'device'
    _sort = 'last desc'
    
    device = fields.Char(
        string=_('Device ID'),
        required=True,
        index=True,
        copy=False,
    )
    bridge_id = fields.Many2one('firebase.bridge',_('Firebase Bridge'))
    user_id = fields.Many2one('res.users',string=_('User'),)
    partner_id = fields.Many2one('res.partner',string=_('Contact'),)
    key = fields.Char(string=_('Key'))
    last = fields.Datetime(_('Last visible'))
    closed = fields.Boolean(_('Closed'))
    active = fields.Boolean(_('Active'), compute='_compute_active', store=True)
    
    @api.depends('last', 'closed')
    def _compute_active(self):
        for record in self:
            record.active = not record.closed and record.last and (fields.Datetime.now() - record.last).total_seconds() <  self.bridge_id.session_timeout
            print("computing active", record, record.user_id.name, record.active, record.closed,record.last,(fields.Datetime.now() - record.last).total_seconds(),self.bridge_id.session_timeout)        
    
    def ping(self):
        for record in self:
            if record.bridge_id.connected:
                msg = {
                    'bridge_id': record.bridge_id.id,
                    'device': record.device,
                    'type': 'ping',
                    'data': {},
                    }
                _logger.info("Pinging %s@%s" % (record.user_id.name, record.key,))
                record.bridge_id.create_message(msg)