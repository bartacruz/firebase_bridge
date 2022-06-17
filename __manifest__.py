# -*- coding: utf-8 -*-
{
    'name': "Firebase Odoo Bridge",

    'summary': """
        Firebase Messaging for Odoo.
        """,

    'description': """
        API-like interface to Odoo using Firebase Messaging
        
    """,

    'author': "Julio Santa Cruz <bartacruz@gmail.com>",
    'category': 'Extra Tools',
    'version': '15.0.0.2',
    "license": "AGPL-3",
    'installable': True,
    'application': True,
    "development_status": "Beta",
    
    # any module necessary for this one to work correctly
    'depends': ['base'],
    
    # always loaded
    'data': [
        'data/ir_sequence.xml',
        'security/ir.model.access.csv',
        'views/firebase_bridge.xml',
        'views/firebase_message.xml',
        'views/firebase_session.xml',
        'views/menu.xml',
    ],

}
