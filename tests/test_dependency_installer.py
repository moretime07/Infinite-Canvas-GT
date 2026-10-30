import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools import install_dependencies as setup


def result(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


class FakePip:
    def __init__(self, installs=None, version="pip 25.0", config="", check=0):
        self.installs = list(installs or [])
        self.version, self.config, self.check = version, config, check
        self.calls = []

    def __call__(self, command, *, env, timeout):
        self.calls.append((command[:], env.copy(), timeout))
        if command[1:] == ["-m", "pip", "--version"]:
            return result(out=self.version)
        if command[1:] == ["-m", "pip", "config", "list"]:
            return result(out=self.config)
        if command[1:] == ["-m", "pip", "check"]:
            return result(self.check)
        if command[1:4] == ["-m", "pip", "install"]:
            response = self.installs.pop(0) if self.installs else result()
            if isinstance(response, Exception):
                raise response
            return response
        raise AssertionError(command)

    def install_calls(self):
        return [call for call in self.calls if call[0][1:4] == ["-m", "pip", "install"]]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="GT 安装测试 ")
        self.addCleanup(directory.cleanup)
        self.requirements = Path(directory.name) / "requirements.txt"
        self.requirements.write_text("fastapi\n", encoding="utf-8")
        self.messages = []

    def install(self, runner, **kwargs):
        setup.install_dependencies(self.requirements, runner=runner, emit=self.messages.append, **kwargs)

    def test_isolates_sources_without_mutating_parent_and_preserves_network(self):
        original = {"PIP_EXTRA_INDEX_URL": "https://unrelated.example/simple", "PIP_TRUSTED_HOST": "bad.example",
                    "PIP_INDEX_URL": "https://unrelated.example/simple", "PIP_TARGET": "somewhere",
                    "PIP_USER": "1", "PIP_CONFIG_FILE": "company.ini", "HTTPS_PROXY": "http://proxy.example:80",
                    "REQUESTS_CA_BUNDLE": "company.pem", "PIP_CLIENT_CERT": "client.pem", "PATH": "python-dir"}
        before = original.copy()
        runner = FakePip(config="global.proxy='http://company.example:8080'\nglobal.cert='ca.pem'\n")
        self.install(runner, environ=original)
        command, env, timeout = runner.install_calls()[0]
        self.assertEqual(original, before)
        self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        self.assertEqual(env["PIP_CERT"], "ca.pem")
        self.assertEqual(env["PIP_CLIENT_CERT"], "client.pem")
        self.assertEqual(env["PIP_PROXY"], "http://company.example:8080")
        self.assertEqual(env["HTTPS_PROXY"], original["HTTPS_PROXY"])
        self.assertEqual(env["REQUESTS_CA_BUNDLE"], "company.pem")
        for key in ("PIP_EXTRA_INDEX_URL", "PIP_TRUSTED_HOST", "PIP_INDEX_URL", "PIP_TARGET", "PIP_USER"):
            self.assertNotIn(key, env)
        self.assertNotIn("--trusted-host", command)
        self.assertIn(setup.SOURCES["pypi"], command)
        self.assertEqual(timeout, 300)
        self.assertEqual(len(runner.install_calls()), 1)  # No unnecessary pip upgrade.

    def test_tls_failure_switches_source_and_does_not_emit_private_error(self):
        private = "https://name:private-password@private.example/simple"
        runner = FakePip([result(1, err="SSLError SSLEOFError from " + private), result()])
        self.install(runner, environ={})
        calls = runner.install_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn(setup.SOURCES["tuna"], calls[1][0])
        self.assertNotIn(private, "\n".join(self.messages))

    def test_timeout_switches_source(self):
        runner = FakePip([subprocess.TimeoutExpired("private command", 300), result()])
        self.install(runner, environ={})
        self.assertEqual(len(runner.install_calls()), 2)

    def test_dependency_conflict_does_not_switch_source(self):
        runner = FakePip([result(1, err="ResolutionImpossible: conflicting requirements")])
        with self.assertRaisesRegex(setup.InstallError, "非网络错误"):
            self.install(runner, environ={})
        self.assertEqual(len(runner.install_calls()), 1)

    def test_explicit_source_and_company_source_never_fall_back(self):
        for kwargs in ({"mode": "tuna"}, {"mode": "configured"}, {"index_url": "https://packages.example/simple"}):
            with self.subTest(kwargs=kwargs):
                runner = FakePip([result(1, err="SSLError")], config="global.index-url='https://company.example/simple'")
                with self.assertRaisesRegex(setup.InstallError, "未关闭 SSL"):
                    self.install(runner, environ={}, **kwargs)
                self.assertEqual(len(runner.install_calls()), 1)

    def test_failed_pip_upgrade_does_not_block_dependencies(self):
        runner = FakePip([result(1, err="SSLError"), result(1, err="SSLError"), result()], version="pip 23.2.1")
        self.install(runner, environ={})
        calls = runner.install_calls()
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][2], 60)
        self.assertIn("--use-feature=truststore", calls[2][0])
        self.assertIn("-r", calls[2][0])
        self.assertTrue(any("继续用已有 pip" in message for message in self.messages))

    def test_updater_can_skip_pip_upgrade(self):
        runner = FakePip(version="pip 23.2.1")
        self.install(runner, environ={}, upgrade_pip=False)
        self.assertEqual(len(runner.install_calls()), 1)
        self.assertNotIn("--upgrade", runner.install_calls()[0][0])

    def test_missing_pip_uses_offline_ensurepip(self):
        runner = FakePip()
        calls = []

        def missing(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return result(1)
            if command[1:3] == ["-m", "ensurepip"]:
                return result()
            return runner(command, **kwargs)

        self.install(missing, environ={})
        self.assertEqual(calls[1][1:], ["-m", "ensurepip", "--upgrade"])

    def test_proxy_and_ca_override_and_url_errors_are_safe(self):
        certificate = self.requirements.with_name("company.pem")
        certificate.write_text("test certificate", encoding="utf-8")
        env = setup.child_environment({}, {}, proxy="http://127.0.0.1:7890", ca_bundle=certificate)
        self.assertEqual(env["PIP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(env["PIP_CERT"], str(certificate.resolve()))
        for url in ("https://[invalid", "http://host:bad", "file:///private", "http://host:0"):
            with self.subTest(url=url), self.assertRaises(setup.InstallError):
                setup.child_environment({}, {}, proxy=url)
        for url in ("https://[invalid", "https://host:bad", "http://host", "https://host#fragment"):
            with self.subTest(url=url), self.assertRaises(setup.InstallError):
                setup.selected_sources("auto", {}, {}, url)

    def test_company_source_env_override_and_output_redaction(self):
        url = "https://user:private-password@packages.example/simple"
        runner = FakePip(config="global.index-url='https://other.example/simple'")
        self.install(runner, environ={"PIP_INDEX_URL": url}, mode="configured")
        self.assertIn(url, runner.install_calls()[0][0])
        self.assertNotIn("private-password", "\n".join(self.messages))

    def test_pip_check_failure_is_not_success(self):
        with self.assertRaisesRegex(setup.InstallError, "缺失或版本冲突"):
            self.install(FakePip(check=1), environ={})

    def test_missing_requirements_or_ca_is_actionable(self):
        with self.assertRaisesRegex(setup.InstallError, "完整解压"):
            setup.install_dependencies(self.requirements.with_name("missing.txt"))
        with self.assertRaisesRegex(setup.InstallError, "CA 证书文件不存在"):
            setup.child_environment({}, {}, ca_bundle=self.requirements.with_name("missing.pem"))


if __name__ == "__main__":
    unittest.main()
