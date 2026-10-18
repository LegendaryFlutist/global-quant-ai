import json
import os
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import patch
import joblib
from lab import demo
from trend_report import load_bars
from features import FEATURES
from notifications import send_email, deliver_once, NotificationError
from signals import infer, signal_once
from strategy import STRATEGY

ENV = dict(QUANT_SMTP_HOST='smtp.example.test', QUANT_SMTP_PORT='465',
           QUANT_SMTP_USER='sender@example.test',QUANT_SMTP_PASSWORD='fake-test-code',
           QUANT_MAIL_FROM='sender@example.test',QUANT_MAIL_TO='reader@example.test')


class NotificationTests(unittest.TestCase):
    def test_ssl_and_missing_config(self):
        """验证真实邮件必须配置齐全并使用校验TLS的SSL连接。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with patch.dict(os.environ,{},clear=True), patch('notifications.smtplib.SMTP_SSL') as smtp:
            with self.assertRaises(NotificationError): send_email('test','body')
            smtp.assert_not_called()
        with patch.dict(os.environ,ENV,clear=True), patch('notifications.smtplib.SMTP_SSL') as smtp:
            send_email('test','body')
            client=smtp.return_value.__enter__.return_value
            client.login.assert_called_once_with(ENV['QUANT_SMTP_USER'],ENV['QUANT_SMTP_PASSWORD'])
            message=client.send_message.call_args.args[0]
            self.assertEqual(message['To'],ENV['QUANT_MAIL_TO'])
            self.assertNotIn(ENV['QUANT_SMTP_PASSWORD'],message.as_string())
            self.assertIn('context',smtp.call_args.kwargs)

    def test_starttls_and_error_redaction(self):
        """验证STARTTLS先于登录且SMTP错误不泄露授权码。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with patch.dict(os.environ,{**ENV,'QUANT_SMTP_SECURITY':'starttls'},clear=True), patch('notifications.smtplib.SMTP') as smtp:
            send_email('test','body')
            client=smtp.return_value.__enter__.return_value
            names=[c[0] for c in client.mock_calls]
            self.assertLess(names.index('starttls'),names.index('login'))
        with patch.dict(os.environ,ENV,clear=True), patch('notifications.smtplib.SMTP_SSL',side_effect=smtplib.SMTPException('fake-test-code')):
            with self.assertRaises(NotificationError) as error: send_email('test','body')
            self.assertNotIn('fake-test-code',str(error.exception))

    def test_success_dedup_failure_retry(self):
        """验证仅成功投递记去重，失败仍可重试。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,ENV,clear=True):
            with patch('notifications.send_email',side_effect=NotificationError('failed')):
                with self.assertRaises(NotificationError): deliver_once('one','subject','body',root)
            with patch('notifications.send_email') as send:
                self.assertEqual(deliver_once('one','subject','body',root),'SMTP_ACCEPTED')
                self.assertEqual(deliver_once('one','subject','body',root),'ALREADY_SENT')
                self.assertEqual(send.call_count,1)

    def test_fresh_inference_and_rejections(self):
        """验证前向推断使用新鲜真实行情并拒绝策略/来源不符。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root:
            d=Path(root)/'data';demo(d,300)
            b=load_bars(d/'bars.csv')
            bundle=dict(name='trend',model=None,threshold=.6,features=FEATURES,
                        source=dict(symbol='BTCUSDT',synthetic=False),
                        research_status='FORWARD_PAPER_REVIEW',strategy=STRATEGY,training_end_ms=int(b.close_time.iloc[-10]))
            source=dict(symbol='BTCUSDT',synthetic=False)
            now=int(b.close_time.iloc[-1])+100
            r=infer(bundle,b,source,now)
            self.assertEqual(r['close_time'],int(b.close_time.iloc[-1]))
            self.assertFalse(r['real_orders_enabled'])
            for model, data, moment in [({**bundle,'research_status':'REJECTED'},source,now),
                    (bundle,{**source,'synthetic':True},now),
                    (bundle,{**source,'symbol':'ETHUSDT'},now),
                    (bundle,source,now+1800000),
                    ({**bundle,'training_end_ms':now},source,now)]:
                with self.assertRaises(ValueError): infer(model,b,data,moment)

    def test_preview_no_send_direction_change_and_retry(self):
        """验证预览不投递、方向变化独立去重且失败可重试。

        Returns:
            无返回值；任一断言失败会使测试失败。
        """
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ,ENV,clear=True):
            p=Path(root);d=p/'data';demo(d,300)
            model=p/'model.joblib';joblib.dump({},model)
            result=dict(symbol='BTCUSDT',model='trend',signal=1,close_time=1700000000000,
                        reference_close=100.,real_orders_enabled=False)
            with patch('signals.infer',return_value=result.copy()),patch('signals.deliver_once') as send:
                signal_once(model,d,p/'preview')
                send.assert_not_called()
                self.assertTrue((p/'preview/email_preview.txt').exists())
            with patch('signals.infer',return_value=result.copy()),patch('signals.deliver_once',side_effect=NotificationError('failed')):
                with self.assertRaises(NotificationError): signal_once(model,d,p/'failed',True,p/'state')
            with patch('signals.infer',return_value=result.copy()),patch('signals.deliver_once',return_value='SMTP_ACCEPTED') as send:
                signal_once(model,d,p/'retry',True,p/'state')
                signal_once(model,d,p/'repeat',True,p/'state')
                self.assertEqual(send.call_count,1)
            result['close_time']+=900000
            with patch('signals.infer',return_value=result.copy()),patch('signals.deliver_once') as send:
                signal_once(model,d,p/'same',True,p/'state')
                send.assert_not_called()
            result.update(signal=-1,close_time=result['close_time']+900000)
            with patch('signals.infer',return_value=result.copy()),patch('signals.deliver_once',return_value='SMTP_ACCEPTED') as send:
                signal_once(model,d,p/'changed',True,p/'state')
                send.assert_called_once()

if __name__=='__main__': unittest.main()
