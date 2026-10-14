"""Run with firmware FastAPI/httpx dependencies; no Bluetooth or motors touched."""
import threading
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import httpx
from fastapi import FastAPI
from gamepad import register_gamepad


class UI:
    def __init__(self): self.app=FastAPI()
    def expose_api(self, method, path, fn): self.app.add_api_route(path, fn, methods=[method])


class GamepadAPITest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker=Mock()
        self.worker.get.return_value.json.return_value={'bluetooth':{'available':True,'devices':[]},'input':{'connected':False}}
        self.worker.post.return_value.status_code=200
        self.controller=Mock(lock=threading.RLock(),input_source='web')
        self.ui=UI()
        with patch('gamepad.httpx.Client',return_value=self.worker):
            self.control=register_gamepad(self.ui,self.controller)
        self.temp=tempfile.TemporaryDirectory()
        self.control.bindings_path=Path(self.temp.name)/'bindings.json'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ui.app),base_url='http://duck:9527')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.control.close()
        self.temp.cleanup()

    async def test_scan_requires_header_and_same_origin(self):
        for headers in ({},{'X-Duck-Gamepad':'1','Origin':'http://other'}):
            r=await self.client.post('/api/gamepad/scan',json={},headers=headers)
            self.assertEqual(r.status_code,403)
        self.worker.post.assert_not_called()
        r=await self.client.post('/api/gamepad/scan',json={},headers={'X-Duck-Gamepad':'1','Origin':'http://duck:9527'})
        self.assertEqual(r.status_code,200)
        self.worker.post.assert_called_once_with('/scan',json={},timeout=10)

    async def test_unknown_paths_and_oversized_body(self):
        headers={'X-Duck-Gamepad':'1'}
        self.assertEqual((await self.client.post('/api/gamepad/shell',json={},headers=headers)).status_code,404)
        self.assertEqual((await self.client.post('/api/gamepad/connect',content=b'x'*4097,headers=headers)).status_code,413)
        self.worker.post.assert_not_called()

    async def test_cannot_enable_without_input(self):
        r=await self.client.post('/api/gamepad/control',json={'enabled':True},headers={'X-Duck-Gamepad':'1'})
        self.assertEqual(r.status_code,400)
        self.controller.set_mode.assert_not_called()
        self.assertFalse(self.control.enabled)

    async def test_usb_switch_requires_origin_header_and_does_not_command_motors(self):
        for headers in ({},{'X-Duck-Gamepad':'1','Origin':'http://other'}):
            r=await self.client.post('/api/gamepad/usb',json={'host':True},headers=headers)
            self.assertEqual(r.status_code,403)
        self.worker.post.assert_not_called()
        r=await self.client.post('/api/gamepad/usb',json={'host':False},headers={'X-Duck-Gamepad':'1'})
        self.assertEqual(r.status_code,200)
        self.worker.post.assert_called_once_with('/usb',json={'host':False},timeout=10)
        self.controller.set_mode.assert_not_called()

    async def test_read_only_status_never_arms(self):
        r=await self.client.get('/api/gamepad/status')
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.json()['control']['enabled'])
        self.controller.set_mode.assert_not_called()

    async def test_bindings_validation_and_persistence(self):
        headers={'X-Duck-Gamepad':'1'}
        r=await self.client.post('/api/gamepad/bindings',json={'operation':'clear','action':'walk'},headers=headers)
        self.assertEqual(r.status_code,200)
        self.assertIsNone(r.json()['control']['bindings']['walk'])
        self.assertTrue(self.control.bindings_path.exists())
        for body in ({'operation':'clear','action':'off'}, {'operation':'learn','action':'shell'}, {'operation':'learn','action':'pick'}):
            r=await self.client.post('/api/gamepad/bindings',json=body,headers=headers)
            self.assertEqual(r.status_code,400)
        r=await self.client.post('/api/gamepad/bindings',json={'operation':'reset'},headers=headers)
        self.assertEqual(r.json()['control']['bindings']['walk'],'start')
        self.worker.post.assert_not_called()
        self.controller.set_mode.assert_not_called()


if __name__=='__main__': unittest.main()
