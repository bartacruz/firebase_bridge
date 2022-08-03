from odoo import _, api, models, fields

class FirebaseSession(models.Model):
    _name = 'firebase.session'
    _description = 'Firebase Bridge device session'
    _rec_name = 'device'
    
    SESSION_TIMEOUT = 60*60 # 1 hour
    
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
            record.active = not record.closed and record.last and (fields.Datetime.now() - record.last).total_seconds() <  self.SESSION_TIMEOUT
            print("computing active", record, record.user_id.name, record.active, record.closed,record.last,(fields.Datetime.now() - record.last).total_seconds(),self.SESSION_TIMEOUT)        
    