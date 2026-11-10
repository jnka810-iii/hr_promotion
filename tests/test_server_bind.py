# -*- coding: utf-8 -*-
"""
เทสต์การเลือก host / debug ของโหมด fallback (`_server_bind`)

ค่าเริ่มต้นต้องเป็น loopback เสมอ เพราะ API ไม่มี authentication การเปิดให้ LAN
ต้องเป็นการเลือกเองผ่าน HR_HOST (run_server.bat) และ debugger ห้ามเปิดคู่กับ LAN
"""

import pytest

import app as hr


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv('HR_HOST', raising=False)
    monkeypatch.delenv('HR_DEBUG', raising=False)


def test_default_is_loopback_without_debug():
    assert hr._server_bind() == ('127.0.0.1', False)


def test_blank_host_falls_back_to_loopback(monkeypatch):
    monkeypatch.setenv('HR_HOST', '   ')
    assert hr._server_bind() == ('127.0.0.1', False)


def test_hr_host_opens_to_lan(monkeypatch):
    monkeypatch.setenv('HR_HOST', '0.0.0.0')
    assert hr._server_bind() == ('0.0.0.0', False)


def test_debug_allowed_on_loopback(monkeypatch):
    monkeypatch.setenv('HR_DEBUG', '1')
    assert hr._server_bind() == ('127.0.0.1', True)


def test_debug_forced_off_when_exposed(monkeypatch):
    """Werkzeug debugger รัน Python ได้ เปิดคู่กับ LAN = ทุกเครื่องรันโค้ดบนเครื่องนี้ได้"""
    monkeypatch.setenv('HR_HOST', '0.0.0.0')
    monkeypatch.setenv('HR_DEBUG', '1')
    assert hr._server_bind() == ('0.0.0.0', False)


def test_main_passes_bind_to_app_run(tmp_path, monkeypatch):
    """main() ต้องใช้ค่าจาก _server_bind ไม่ใช่ host ที่ฝังไว้"""
    monkeypatch.setattr(hr, '_setup_logging', lambda: None)
    monkeypatch.setattr(hr, '_backup_db', lambda: None)
    monkeypatch.setattr(hr, 'init_db', lambda: None)
    monkeypatch.setattr(hr, 'HAS_WEBVIEW', False)
    seen = {}
    monkeypatch.setattr(hr.app, 'run', lambda **k: seen.update(k))
    monkeypatch.setenv('HR_HOST', '0.0.0.0')
    monkeypatch.setenv('HR_DEBUG', '1')

    hr.main()

    assert seen['host'] == '0.0.0.0'
    assert seen['debug'] is False
