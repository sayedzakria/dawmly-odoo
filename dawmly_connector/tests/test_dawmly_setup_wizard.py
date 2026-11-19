from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDawmlySetupWizard(TransactionCase):
    CONNECTOR_LOGIN = "dawmly-connector"
    KEY_NAME = "Dawmly Integration"
    JOURNAL_NAME = "Dawmly"
    JOURNAL_CODE = "DAWM"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal_group = cls.env.ref("base.group_user")
        cls.system_group = cls.env.ref("base.group_system")
        cls.billing_group = cls.env.ref("account.group_account_invoice")
        cls.account_user_group = cls.env.ref("account.group_account_user")

        cls.admin_user = cls.env["res.users"].create(
            {
                "name": "Dawmly Test Admin",
                "login": "dawmly_test_admin",
                "email": "dawmly_test_admin@example.com",
                "group_ids": [(6, 0, [cls.internal_group.id, cls.system_group.id])],
            }
        )
        cls.non_admin_user = cls.env["res.users"].create(
            {
                "name": "Dawmly Test Billing User",
                "login": "dawmly_test_billing",
                "email": "dawmly_test_billing@example.com",
                "group_ids": [(6, 0, [cls.internal_group.id, cls.billing_group.id])],
            }
        )

    def test_setup_creates_user_persistent_key_and_journal(self):
        wizard = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        wizard.action_setup()

        self.assertEqual(wizard.state, "ready")
        self.assertTrue(wizard.odoo_url)
        self.assertEqual(wizard.database_name, self.env.cr.dbname)
        self.assertEqual(wizard.username, self.CONNECTOR_LOGIN)
        self.assertTrue(wizard.api_key)
        self.assertEqual(len(wizard.api_key), 40)

        connector_user = (
            self.env["res.users"]
            .with_context(active_test=False)
            .search([("login", "=", self.CONNECTOR_LOGIN)], limit=1)
        )
        self.assertTrue(connector_user)
        self.assertTrue(connector_user.active)
        self.assertFalse(connector_user.share)
        self.assertIn(self.internal_group, connector_user.all_group_ids)
        self.assertIn(self.billing_group, connector_user.all_group_ids)
        self.assertNotIn(self.system_group, connector_user.all_group_ids)
        self.assertNotIn(self.account_user_group, connector_user.all_group_ids)

        api_keys = (
            self.env["res.users.apikeys"]
            .sudo()
            .search([("user_id", "=", connector_user.id), ("name", "=", self.KEY_NAME)])
        )
        self.assertEqual(len(api_keys), 1)
        self.assertFalse(api_keys.scope)
        self.assertFalse(api_keys.expiration_date)

        auth_uid = (
            self.env["res.users.apikeys"]
            .sudo()
            ._check_credentials(scope="rpc", key=wizard.api_key)
        )
        self.assertEqual(auth_uid, connector_user.id)

        journal = self.env["account.journal"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("type", "=", "general"),
                ("code", "=", self.JOURNAL_CODE),
            ],
            limit=1,
        )
        self.assertTrue(journal)
        self.assertEqual(journal.name, self.JOURNAL_NAME)

        expense_acc = self.env["account.account"].create(
            {
                "name": "Dawmly Test Salary Expense",
                "code": "990101",
                "account_type": "expense",
            }
        )
        payable_acc = self.env["account.account"].create(
            {
                "name": "Dawmly Test Net Payable",
                "code": "990201",
                "account_type": "liability_current",
            }
        )

        accounts = (
            self.env["account.account"]
            .with_user(connector_user)
            .search_read([("code", "in", ["990101", "990201"])], ["id", "code", "name"])
        )
        self.assertEqual(len(accounts), 2)

        journals = (
            self.env["account.journal"]
            .with_user(connector_user)
            .search_read([("code", "=", self.JOURNAL_CODE)], ["id", "code", "name", "type"])
        )
        self.assertEqual(len(journals), 1)

        move = (
            self.env["account.move"]
            .with_user(connector_user)
            .create(
                {
                    "move_type": "entry",
                    "journal_id": journal.id,
                    "ref": "DAWMLY-TEST-RUN-1",
                    "line_ids": [
                        (
                            0,
                            0,
                            {
                                "account_id": expense_acc.id,
                                "name": "Basic Salary",
                                "debit": 1000.0,
                                "credit": 0.0,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "account_id": payable_acc.id,
                                "name": "Net Payable",
                                "debit": 0.0,
                                "credit": 1000.0,
                            },
                        ),
                    ],
                }
            )
        )
        self.assertEqual(move.state, "draft")
        self.assertEqual(move.journal_id, journal)

    def test_setup_is_idempotent_and_revokes_previous_key(self):
        wizard_1 = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        wizard_1.action_setup()
        first_key = wizard_1.api_key
        self.assertTrue(first_key)

        user_1 = self.env["res.users"].search([("login", "=", self.CONNECTOR_LOGIN)])
        journal_1 = self.env["account.journal"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("type", "=", "general"),
                ("code", "=", self.JOURNAL_CODE),
            ]
        )
        self.assertEqual(len(user_1), 1)
        self.assertEqual(len(journal_1), 1)

        wizard_2 = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        self.assertTrue(wizard_2.existing_key_warning)
        wizard_2.action_setup()
        second_key = wizard_2.api_key
        self.assertTrue(second_key)
        self.assertNotEqual(second_key, first_key)

        users = self.env["res.users"].search([("login", "=", self.CONNECTOR_LOGIN)])
        journals = self.env["account.journal"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("type", "=", "general"),
                ("code", "=", self.JOURNAL_CODE),
            ]
        )
        self.assertEqual(users.ids, user_1.ids)
        self.assertEqual(journals.ids, journal_1.ids)

        api_keys = (
            self.env["res.users.apikeys"]
            .sudo()
            .search([("user_id", "=", user_1.id), ("name", "=", self.KEY_NAME)])
        )
        self.assertEqual(len(api_keys), 1)

        self.assertEqual(
            self.env["res.users.apikeys"]
            .sudo()
            ._check_credentials(scope="rpc", key=second_key),
            user_1.id,
        )
        self.assertFalse(
            self.env["res.users.apikeys"]
            .sudo()
            ._check_credentials(scope="rpc", key=first_key)
        )

    def test_non_system_user_gets_access_error(self):
        with self.assertRaises(AccessError):
            self.env["dawmly.setup.wizard"].with_user(self.non_admin_user).create({})

    def test_preexisting_unmanaged_user_raises_user_error(self):
        preexisting = self.env["res.users"].create(
            {
                "name": "Preexisting Connector Login",
                "login": self.CONNECTOR_LOGIN,
                "password": "known-preexisting-password",
                "group_ids": [(6, 0, [self.internal_group.id, self.system_group.id])],
            }
        )
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s", [preexisting.id]
        )
        hash_before = self.env.cr.fetchone()[0]
        groups_before = set(preexisting.group_ids.ids)

        wizard = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        with self.assertRaises(UserError):
            wizard.action_setup()

        preexisting.invalidate_recordset()
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s", [preexisting.id]
        )
        hash_after = self.env.cr.fetchone()[0]
        self.assertEqual(hash_after, hash_before)
        self.assertEqual(set(preexisting.group_ids.ids), groups_before)
        self.assertFalse(
            self.env["res.users.apikeys"]
            .sudo()
            .search([("user_id", "=", preexisting.id)])
        )
        self.assertFalse(
            self.env.ref("dawmly_connector.connector_user", raise_if_not_found=False)
        )

    def test_second_setup_resets_groups_and_revokes_all_keys(self):
        wizard_1 = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        wizard_1.action_setup()
        first_key = wizard_1.api_key

        connector_user = self.env["res.users"].search(
            [("login", "=", self.CONNECTOR_LOGIN)], limit=1
        )
        self.assertEqual(
            self.env.ref("dawmly_connector.connector_user", raise_if_not_found=False),
            connector_user,
        )

        connector_user.sudo().write({"group_ids": [(4, self.system_group.id)]})
        self.assertIn(self.system_group, connector_user.group_ids)

        other_key = (
            self.env["res.users.apikeys"]
            .with_user(connector_user)
            .sudo()
            ._generate(None, "other", None)
        )

        wizard_2 = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        self.assertTrue(wizard_2.existing_key_warning)
        wizard_2.action_setup()

        connector_user.invalidate_recordset()
        self.assertEqual(
            set(connector_user.group_ids.ids),
            {self.internal_group.id, self.billing_group.id},
        )
        self.assertIn(self.internal_group, connector_user.all_group_ids)
        self.assertIn(self.billing_group, connector_user.all_group_ids)
        self.assertNotIn(self.system_group, connector_user.all_group_ids)

        all_keys = (
            self.env["res.users.apikeys"]
            .sudo()
            .search([("user_id", "=", connector_user.id)])
        )
        self.assertEqual(len(all_keys), 1)
        self.assertEqual(all_keys.name, self.KEY_NAME)
        self.assertEqual(
            self.env["res.users.apikeys"]
            .sudo()
            ._check_credentials(scope="rpc", key=wizard_2.api_key),
            connector_user.id,
        )
        self.assertFalse(
            self.env["res.users.apikeys"]
            .sudo()
            ._check_credentials(scope="rpc", key=first_key)
        )
        self.assertFalse(
            self.env["res.users.apikeys"]
            .sudo()
            ._check_credentials(scope="rpc", key=other_key)
        )

    def test_second_setup_resets_connector_user_password(self):
        wizard_1 = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        wizard_1.action_setup()
        connector_user = self.env["res.users"].search(
            [("login", "=", self.CONNECTOR_LOGIN)], limit=1
        )

        connector_user.sudo().write({"password": "temporary-test-password"})
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s", [connector_user.id]
        )
        hash_before = self.env.cr.fetchone()[0]
        self.assertTrue(hash_before)

        wizard_2 = self.env["dawmly.setup.wizard"].with_user(self.admin_user).create({})
        wizard_2.action_setup()
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s", [connector_user.id]
        )
        hash_after = self.env.cr.fetchone()[0]
        self.assertTrue(hash_after)
        self.assertNotEqual(hash_after, hash_before)
