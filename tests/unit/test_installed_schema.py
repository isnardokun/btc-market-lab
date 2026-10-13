from contextlib import closing
import importlib.util
import sqlite3
import unittest
from tests.unit.installed_fixture_helpers import new_fixture


class InstalledSchemaTests(unittest.TestCase):
    def setUp(self):
        self.root,self.path,self.locks = new_fixture(self)

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('storage.installed_schema'), 'Installed profile validator is missing')
        from storage import installed_schema
        return installed_schema

    def test_profile_and_all_34_tables(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            result=self.module().inspect_installed_schema(db)
        self.assertEqual(result['state'],'INSTALLED_STRUCTURE_MATCH')
        self.assertEqual(result['legacy_tables'],34)
        self.assertEqual(result['objects'],54)
        self.assertEqual(result['scope'],'STRUCTURAL_METADATA_NOT_COMPLETE_DDL')

    def test_missing_or_changed_contract(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('ALTER TABLE price_btc ADD COLUMN close REAL')
            with self.assertRaisesRegex(ValueError,'SCHEMA_DRIFT'):
                self.module().inspect_installed_schema(db)

    def test_fingerprints_include_legacy_rows_and_trigger_sql(self):
        module=self.module()
        with closing(sqlite3.connect(self.path)) as db, db:
            first=module.legacy_fingerprint(db)
            db.execute('UPDATE price_btc SET price=1 WHERE id=1')
            changed=module.legacy_fingerprint(db)
            self.assertNotEqual(first['tables']['price_btc'],changed['tables']['price_btc'])
            self.assertEqual(first['schema_sha256'],changed['schema_sha256'])
            db.execute('DROP TRIGGER trg_archive_bitview_revisions')
            db.execute('CREATE TRIGGER trg_archive_bitview_revisions AFTER UPDATE ON daily BEGIN SELECT 2; END')
            self.assertNotEqual(changed['schema_sha256'],module.legacy_fingerprint(db)['schema_sha256'])

    def test_versions_and_extra_objects(self):
        module=self.module()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO market_context_migrations VALUES(4,'SYNTHETIC')")
            with self.assertRaisesRegex(ValueError,'MIGRATION_VERSION_DRIFT'): module.inspect_installed_schema(db)
            db.execute('DELETE FROM market_context_migrations WHERE version=4')
            db.execute('CREATE TABLE surprise(value TEXT)')
            with self.assertRaisesRegex(ValueError,'OBJECT_DRIFT'): module.inspect_installed_schema(db)

    def test_index_and_fk_drift_fail_closed(self):
        module=self.module()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DROP INDEX idx_daily_series_ts')
            db.execute('CREATE INDEX idx_daily_series_ts ON daily(ts,series_id)')
            with self.assertRaisesRegex(ValueError,'INDEX_DRIFT'): module.inspect_installed_schema(db)


if __name__=='__main__': unittest.main()
