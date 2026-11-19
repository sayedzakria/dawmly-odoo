{
    "name": "Dawmly — Attendance & Payroll for Egypt",
    "version": "17.0.1.0.0",
    "category": "Human Resources/Payroll",
    "summary": "Guided setup for Dawmly Attendance & Payroll journal integration",
    "author": "Dawmly",
    "website": "https://dawmly.com/features/odoo-integration/",
    "support": "hello@dawmly.com",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "wizard/dawmly_setup_wizard_views.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
    "application": True,
}
