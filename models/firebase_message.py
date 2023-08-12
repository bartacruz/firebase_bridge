import logging
import json
from odoo import _, api, models, fields
from odoo.tools import date_utils

logger = logging.getLogger(__name__)
class FirebaseMessage(models.Model):
    _name = 'firebase.message'
    _description = 'Firebase Message'
    
    name = fields.Char(
        string="Name",
        required=True,
        index=True,
        copy=False,
        default=lambda self: _("New"),
    )
    bridge_id = fields.Many2one('firebase.bridge', _('Firebase Bridge') )
    partner_id = fields.Many2one('res.partner', _('Partner') )
    device = fields.Char(_('Device ID'))
    type = fields.Char(_('Message type'))
    model = fields.Char(_('Model'))
    data = fields.Text(_('Message content'))
    created = fields.Datetime(_('Created'), default=fields.Datetime.now, required=True)
    sent = fields.Datetime(_('Sent'))
    
    @api.model
    def create(self, vals_list):
        if vals_list.get("name", _("New")) == _("New"):
            vals_list["name"] = self.env["ir.sequence"].next_by_code(
                "firebase.message") or _("New")
        ret = super(FirebaseMessage, self).create(vals_list)
        return ret
    
    @api.model
    def _cron_delete_old_pings(self, max=10000):
        pings = self.search(['&',('type','=','ping'),('sent','!=',None)],limit=max)
        pings_found = len(pings)
        pings.unlink()
        pings_left = self.search_count(['&',('type','=','ping'),('sent','!=',None)])
        logger.info('Pings deleted:%s. left:%s',pings_found,pings_left)
