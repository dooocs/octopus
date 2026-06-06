from __future__ import annotations

import unittest

from scripts.aliyun_rds_test_insert import build_record


class AliyunRdsSmokeScriptTest(unittest.TestCase):
    def test_build_record_exposes_smoke_contract(self) -> None:
        record = build_record()

        self.assertEqual(record.source_type, "system")
        self.assertEqual(record.sub_source_type, "aliyun_rds_test")
        self.assertEqual(record.item_type, "smoke_test")
        self.assertEqual(record.title, "Octopus Aliyun RDS smoke test")
        self.assertIn("workflow", record.context_content or {})


if __name__ == "__main__":
    unittest.main()
