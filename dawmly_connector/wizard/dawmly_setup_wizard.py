import secrets

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError


class DawmlySetupWizard(models.TransientModel):
    _name = "dawmly.setup.wizard"
    _description = "Dawmly Integration Setup"

    _CONNECTOR_LOGIN = "dawmly-connector"
    _CONNECTOR_NAME = "Dawmly Connector"
    _KEY_NAME = "Dawmly Integration"
    _JOURNAL_NAME = "Dawmly"
    _JOURNAL_CODE = "DAWM"
    _USER_XMLID_MODULE = "dawmly_connector"
    _USER_XMLID_NAME = "connector_user"

    state = fields.Selection(
        [("intro", "Introduction"), ("ready", "Ready")],
        default="intro",
        readonly=True,
    )
    existing_key_warning = fields.Boolean(
        compute="_compute_existing_key_warning",
        readonly=True,
    )
    odoo_url = fields.Char(
        string="Odoo URL",
        help="Public https:// URL of this Odoo instance reachable from the internet.",
    )
    database_name = fields.Char(string="Database", readonly=True)
    username = fields.Char(string="Username", readonly=True)
    api_key = fields.Char(string="API Key", readonly=True)
    user_status = fields.Char(string="Connector User", readonly=True)
    journal_status = fields.Char(string="Payroll Journal", readonly=True)

    @api.depends("state")
    def _compute_existing_key_warning(self):
        connector_user = self._get_tracked_connector_user()
        has_key = bool(
            connector_user
            and self.env["res.users.apikeys"]
            .sudo()
            .search_count([("user_id", "=", connector_user.id)])
        )
        for record in self:
            record.existing_key_warning = has_key

    def action_setup(self):
        self.ensure_one()
        if not self.env.is_system():
            raise AccessError(
                _("Only Settings administrators can set up the Dawmly connection.")
            )

        connector_user, user_status = self._ensure_connector_user()
        journal_status = self._ensure_dawmly_journal()
        api_key, revoked_count = self._rotate_connector_api_key(connector_user)
        if revoked_count:
            user_status = _("%s (previous API key(s) revoked)") % user_status

        base_url = (
            self.odoo_url
            or self.env["ir.config_parameter"].sudo().get_param("web.base.url")
            or ""
        ).rstrip("/")

        self.write(
            {
                "state": "ready",
                "odoo_url": base_url,
                "database_name": self.env.cr.dbname,
                "username": connector_user.login,
                "api_key": api_key,
                "user_status": user_status,
                "journal_status": journal_status,
            }
        )
        return {
            "name": _("Dawmly Setup"),
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_open_dawmly(self):
        return {
            "type": "ir.actions.act_url",
            "url": "https://app.dawmly.com/integrations",
            "target": "new",
        }

    def _get_tracked_connector_imd_and_user(self):
        imd = (
            self.env["ir.model.data"]
            .sudo()
            .search(
                [
                    ("module", "=", self._USER_XMLID_MODULE),
                    ("name", "=", self._USER_XMLID_NAME),
                ],
                limit=1,
            )
        )
        if not imd or imd.model != "res.users":
            return imd, self.env["res.users"]
        user = (
            self.env["res.users"]
            .sudo()
            .with_context(active_test=False)
            .browse(imd.res_id)
            .exists()
        )
        return imd, user

    def _get_tracked_connector_user(self):
        _, user = self._get_tracked_connector_imd_and_user()
        return user

    def _ensure_connector_user(self):
        company = self.env.company
        internal_group = self.env.ref("base.group_user")
        billing_group = self.env.ref("account.group_account_invoice")
        users_model = self.env["res.users"].sudo()
        imd_model = self.env["ir.model.data"].sudo()

        imd, connector_user = self._get_tracked_connector_imd_and_user()

        login_domain = [("login", "=", self._CONNECTOR_LOGIN)]
        if connector_user:
            login_domain.append(("id", "!=", connector_user.id))
        conflicting_user = users_model.with_context(active_test=False).search(
            login_domain, limit=1
        )
        if conflicting_user:
            raise UserError(
                _(
                    "A user with login '%s' already exists and is not managed by Dawmly. "
                    "Please rename or delete that user before running Dawmly Setup."
                )
                % self._CONNECTOR_LOGIN
            )

        exact_groups = [Command.set([internal_group.id, billing_group.id])]
        unusable_password = secrets.token_urlsafe(48)

        if connector_user:
            connector_user.write(
                {
                    "login": self._CONNECTOR_LOGIN,
                    "active": True,
                    "share": False,
                    "password": unusable_password,
                    "company_id": connector_user.company_id.id or company.id,
                    "company_ids": [Command.link(company.id)],
                    "group_ids": exact_groups,
                }
            )
            return connector_user, _("Reused user '%s'") % connector_user.login

        connector_user = users_model.create(
            {
                "name": self._CONNECTOR_NAME,
                "login": self._CONNECTOR_LOGIN,
                "password": unusable_password,
                "share": False,
                "active": True,
                "company_id": company.id,
                "company_ids": [Command.set([company.id])],
                "group_ids": exact_groups,
            }
        )
        if imd:
            imd.write(
                {
                    "model": "res.users",
                    "res_id": connector_user.id,
                    "noupdate": True,
                }
            )
        else:
            imd_model.create(
                {
                    "module": self._USER_XMLID_MODULE,
                    "name": self._USER_XMLID_NAME,
                    "model": "res.users",
                    "res_id": connector_user.id,
                    "noupdate": True,
                }
            )
        return connector_user, _("Created user '%s' with Billing rights") % self._CONNECTOR_LOGIN

    def _ensure_dawmly_journal(self):
        company = self.env.company
        journal_model = self.env["account.journal"].sudo()
        journal = journal_model.search(
            [
                ("company_id", "=", company.id),
                ("type", "=", "general"),
                "|",
                ("code", "=", self._JOURNAL_CODE),
                ("name", "=", self._JOURNAL_NAME),
            ],
            limit=1,
        )
        if journal:
            return _("Reused journal '%s' (%s)") % (journal.name, journal.code)

        journal = journal_model.create(
            {
                "name": self._JOURNAL_NAME,
                "code": self._JOURNAL_CODE,
                "type": "general",
                "company_id": company.id,
            }
        )
        return _("Created journal '%s' (%s)") % (journal.name, journal.code)

    def _rotate_connector_api_key(self, connector_user):
        apikeys_env = self.env["res.users.apikeys"].with_user(connector_user).sudo()
        existing_keys = apikeys_env.search([("user_id", "=", connector_user.id)])
        revoked_count = len(existing_keys)
        if existing_keys:
            existing_keys._remove()
        api_key = apikeys_env._generate(None, self._KEY_NAME, None)
        return api_key, revoked_count
