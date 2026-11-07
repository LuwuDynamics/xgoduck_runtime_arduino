"""Explicit server selection, persistence, isolation and same-origin HTTP contract."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from voice_config import VoiceConfig
from voice_region import register_voice_region


class VoiceRegionTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = VoiceConfig(self.root / 'voice_config.json')

    def test_first_selection_is_explicit_and_read_does_not_create_config(self):
        self.assertEqual(self.config.region_status(),dict(cloud_region=None,custom_endpoint=False,configured=False,wake_phrase=None))
        self.assertFalse(self.config.path.exists())
        result=self.config.select_region('global')
        self.assertTrue(result['changed'])
        self.assertEqual(result['wake_phrase'], 'Hey Ducky')
        self.assertEqual(VoiceConfig(self.config.path).get()['cloud_region'],'global')
        self.assertEqual(self.config.get()['language'],'cn')

    def test_switch_preserves_identity_calibration_permissions_and_audio(self):
        self.config.update(dict(cloud_region='cn',language='en',volume=73,input_gain_db=4,
                                voice_motion_enabled=True,remote_motion_enabled=True))
        identity=self.root/'device-id';identity.write_text('02:00:00:00:01:23')
        calibration=self.root/'imu-calibration.json';calibration.write_text('fixture')
        before=self.config.get()
        self.config.select_region('global')
        expected=dict(before,cloud_region='global',ota_url='')
        self.assertEqual(self.config.get(),expected)
        self.assertEqual(identity.read_text(),'02:00:00:00:01:23')
        self.assertEqual(calibration.read_text(),'fixture')
        self.assertEqual(self.config.select_region('cn')['wake_phrase'], '小鸭小鸭')
        self.assertEqual(self.config.get(),before)

    def test_same_region_is_idempotent_and_does_not_rewrite_or_reconnect(self):
        self.config.select_region('cn');before=self.config.path.stat().st_mtime_ns
        result=self.config.select_region('cn')
        self.assertFalse(result['changed']);self.assertFalse(result['reconnecting'])
        self.assertEqual(self.config.path.stat().st_mtime_ns,before)

    def test_explicit_selection_clears_custom_url_and_status_never_exposes_it(self):
        self.config.update(dict(cloud_region='cn',ota_url='https://example.invalid/ota/?token=fixture-secret'))
        self.assertNotIn('fixture-secret',json.dumps(self.config.region_status()))
        self.assertTrue(self.config.select_region('cn')['changed'])
        self.assertEqual(self.config.get()['ota_url'],'')

    def test_corrupt_config_and_invalid_selections_are_never_replaced(self):
        for body in ['broken json',json.dumps({'cloud_region':'auto'})]:
            self.config.path.write_text(body)
            with self.assertRaises(ValueError):self.config.select_region('cn')
            self.assertEqual(self.config.path.read_text(),body)
        self.config.path.unlink()
        for value in [None,True,0,'auto','CN',[],{}]:
            with self.assertRaises(ValueError):self.config.select_region(value)
        self.assertFalse(self.config.path.exists())

    def client(self,failed=False):
        app=FastAPI()
        ui=SimpleNamespace(expose_api=lambda method,path,callback:app.add_api_route(path,callback,methods=[method]))
        def call(path,body):
            self.assertEqual(path,'/region')
            if failed:raise OSError('voice unavailable')
            return self.config.select_region(body['cloud_region'])
        voice=SimpleNamespace(call=Mock(side_effect=call))
        register_voice_region(ui,voice,self.config)
        return TestClient(app),voice

    def test_http_get_is_read_only_and_explicit_post_uses_voice_ipc(self):
        client,voice=self.client()
        self.assertFalse(client.get('/api/voice/region').json()['configured'])
        voice.call.assert_not_called()
        result=client.post('/api/voice/region',json={'cloud_region':'global'},headers={'X-Device-Test':'1','Origin':'http://testserver'})
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.json()['changed'])
        self.assertEqual(result.json()['wake_phrase'], 'Hey Ducky')
        self.assertEqual(self.config.get()['cloud_region'],'global')
        voice.call.assert_called_once()

    def test_http_rejects_foreign_origin_missing_header_and_extra_fields(self):
        client,voice=self.client()
        for headers in [{},{'X-Device-Test':'1','Origin':'https://other.invalid'}]:
            self.assertEqual(client.post('/api/voice/region',json={'cloud_region':'cn'},headers=headers).status_code,403)
        for data in [None,[],{'cloud_region':'auto'},{'cloud_region':'cn','language':'en'},{'ota_url':'https://other.invalid'}]:
            self.assertEqual(client.post('/api/voice/region',json=data,headers={'X-Device-Test':'1'}).status_code,400)
        self.assertEqual(client.post('/api/voice/region',content=b'x'*1025,headers={'X-Device-Test':'1'}).status_code,413)
        voice.call.assert_not_called();self.assertFalse(self.config.path.exists())

    def test_unavailable_voice_returns_error_without_saving_and_corruption_is_reported(self):
        client,voice=self.client(failed=True)
        result=client.post('/api/voice/region',json={'cloud_region':'cn'},headers={'X-Device-Test':'1'})
        self.assertEqual(result.status_code,503);self.assertFalse(self.config.path.exists())
        self.config.path.write_text('broken json')
        self.assertEqual(client.get('/api/voice/region').status_code,409)


if __name__=='__main__':unittest.main()
