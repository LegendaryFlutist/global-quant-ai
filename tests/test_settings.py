import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from settings import load_local_env


class SettingsTests(unittest.TestCase):
    def test_local_settings_no_execution_and_env_precedence(self):
        """验证本机配置不执行shell且已有环境变量优先。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root,patch.dict(os.environ,{'QUANT_MAIL_TO':'existing@example.test'},clear=True):
            p=Path(root)/'.env'
            p.write_text("QUANT_MAIL_TO=local@example.test\nQUANT_SMTP_PASSWORD='$(not-a-command) with spaces'\n")
            load_local_env(p)
            self.assertEqual(os.environ['QUANT_MAIL_TO'],'existing@example.test')
            self.assertEqual(os.environ['QUANT_SMTP_PASSWORD'],'$(not-a-command) with spaces')

    def test_invalid_configuration_does_not_leak_value(self):
        """验证配置异常不泄露原始敏感值。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root:
            p=Path(root)/'.env';p.write_text("QUANT_SMTP_PASSWORD='fake-secret\n")
            with self.assertRaises(ValueError) as error: load_local_env(p)
            self.assertNotIn('fake-secret',str(error.exception))

if __name__=='__main__': unittest.main()
