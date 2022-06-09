# -*- coding: utf-8 -*-
{
    'name': "Firebase Odoo Bridge",

    'summary': """
        Firebase Messaging for Odoo.
        """,

    'description': """
        API-like interface to Odoo using Firebase Messaging
        
    """,

    'author': "BartaTech",
    'website': "http://www.bartatech.com",
    'category': 'Extra Tools',
    'version': '15.0.0.1',
    "license": "AGPL-3",
    'installable': True,
    'application': True,
    "development_status": "Alpha",
    
    # any module necessary for this one to work correctly
    'depends': ['base'],
    
    # always loaded
    'data': [
        'security/ir.model.access.csv',
        'views/firebase_bridge.xml',
        'views/firebase_session.xml',
        'views/menu.xml',
    ],

}
