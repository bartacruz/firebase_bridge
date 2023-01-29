import json
from odoo import _, api, models, fields
from odoo.tools import date_utils
import logging

logger = logging.getLogger(__name__)
class FirebaseMixin(models.AbstractModel):
    _name = 'firebase.mixin'
    _description = "mixin to send"
    
    def _to_firebase_data(self):
        return json.dumps(self.read()[0], default=date_utils.json_default)
    
    def _firebase_send(self,partner_id, ev=None):
        ev = ev or self._to_firebase_data()
        bridge = self._get_default_bridge()
        if bridge and bridge.connected :
            bridge.send_to_partner(partner_id,self._name,ev)

    @api.model        
    def _firebase_is_active(self,partner_id):
        bridge = self._get_default_bridge()
        sessions = bridge.session_ids.filtered(lambda x: x.partner_id.id == partner_id)
        return any(s.is_active for s in sessions)
    
    @api.model
    def _get_default_bridge(self):
        bridge_id = self.env['ir.config_parameter'].sudo().get_param('towing.firebase_bridge')
        bridge = self.env['firebase.bridge'].sudo().browse(int(bridge_id))
        return bridge
    