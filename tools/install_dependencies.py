"""Safe base-dependency installation, shared by setup and the GT updater.

Only child-process pip configuration is changed. Existing pip proxy/CA settings
are read, never modified. API files and download-proxy settings are not accessed;
system certificates are not changed. Raw pip output may contain authenticated
URLs, so it is never printed or written to a log here.
"""
import argparse
import ast
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit


SOURCES = {
    "pypi": "https://pypi.org/simple",
    "tuna": "https://pypi.tuna.tsinghua.edu.cn/simple",
}
NETWORK_OPTIONS = {"proxy": "PIP_PROXY", "cert": "PIP_CERT", "client-cert": "PIP_CLIENT_CERT"}
NETWORK_ERROR = re.compile(
    r"sslerror|ssleoferror|certificate_verify_failed|connection broken|connectionerror|"
    r"connecttimeout|readtimeout|read timed out|connect timed out|newconnectionerror|"
    r"proxyerror|connection reset|connection aborted|failed to establish|"
    r"temporary failure in name resolution|name or service not known|getaddrinfo failed|"
    r"network is unreachable|could not fetch url|too many (?:429|50[0234]) error",
    re.I,
)


class InstallError(RuntimeError):
    """Only non-sensitive, user-facing summaries should be stored here."""


def report(message):
    print(message, flush=True)


def run_process(command, *, env, timeout):
    return subprocess.run(command, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def pip_configuration(python, env, runner):
    try:
        result = runner([python, "-m", "pip", "config", "list"], env=env, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("无法读取 pip 配置。请检查 pip.ini 的格式；未忽略其中的公司代理或证书设置。") from None
    if result.returncode:
        raise InstallError("无法读取 pip 配置。请检查 pip.ini 的格式；不会打印可能包含密码的配置内容。")
    settings = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(parsed, str):
                settings[key.strip()] = parsed
    return settings


def child_environment(original, settings, *, proxy=None, ca_bundle=None):
    # Avoid inherited extra-index, trusted-host, target/user/python and index
    # settings. In particular a broken NVIDIA extra index must not stall pip.
    env = {key: value for key, value in original.items() if not key.upper().startswith("PIP_")}
    for option, name in NETWORK_OPTIONS.items():
        value = original.get(name) or settings.get(f"install.{option}") or settings.get(f"global.{option}")
        if value:
            env[name] = value
    if proxy:
        try:
            parsed = urlsplit(proxy)
            valid = parsed.scheme in {"http", "https"} and parsed.hostname and (parsed.port is None or parsed.port > 0)
        except ValueError:
            valid = False
        if not valid:
            raise InstallError("安装代理必须是 HTTP/HTTPS 代理地址，例如 http://127.0.0.1:7890。")
        env["PIP_PROXY"] = proxy
    if ca_bundle:
        if not Path(ca_bundle).is_file():
            raise InstallError("指定的 CA 证书文件不存在，请使用公司 IT 提供的可信 CA 文件。")
        env["PIP_CERT"] = str(Path(ca_bundle).resolve())
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1",
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    # HTTP(S)_PROXY / ALL_PROXY / NO_PROXY and CA bundle environment variables
    # remain intact. TLS validation is never disabled.
    return env


def selected_sources(mode, settings, original, index_url=None):
    if index_url or mode == "configured":
        url = index_url or original.get("PIP_INDEX_URL") or settings.get("install.index-url") or settings.get("global.index-url") or SOURCES["pypi"]
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme == "https" and parsed.hostname and not parsed.fragment and (parsed.port is None or parsed.port > 0)
        except ValueError:
            valid = False
        if not valid:
            raise InstallError("自定义安装源必须是有效的 HTTPS 地址。")
        return [("自定义 HTTPS 源", url)]  # Never fall back from a company source to a public mirror.
    if mode == "auto":
        return [("PyPI 官方源", SOURCES["pypi"]), ("清华 TUNA 镜像", SOURCES["tuna"])]
    return [("PyPI 官方源" if mode == "pypi" else "清华 TUNA 镜像", SOURCES[mode])]


def pip_install(python, arguments, sources, env, runner, emit, *, wall_timeout=300, truststore=False):
    for position, (label, url) in enumerate(sources):
        emit(f"正在使用{label}安装；网络重试最多 1 次，当前步骤最长 {wall_timeout} 秒…")
        command = [python, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                   "--progress-bar", "off", "--timeout", "20", "--retries", "1", "--index-url", url]
        if truststore:
            command += ["--use-feature=truststore"]
        command += arguments
        try:
            result = runner(command, env=env, timeout=wall_timeout)
            if result.returncode == 0:
                emit(f"{label}：完成。")
                return sources[position:] + sources[:position]
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            network_failure = bool(NETWORK_ERROR.search(output))
        except subprocess.TimeoutExpired:
            network_failure = True
        except OSError:
            raise InstallError("无法启动项目的 pip，请重新检查 Python 环境。") from None
        if not network_failure:
            raise InstallError("依赖安装失败（非网络错误）。请检查 Python 版本、包兼容性、磁盘空间或构建工具；未自动更换下载源。")
        if position + 1 < len(sources):
            emit(f"{label}连接失败或超时；保持证书验证，切换备用下载源。")
    raise InstallError("下载源连接失败：请检查网络或有效的安装代理；公司 HTTPS 网络请指定可信 CA 证书。未关闭 SSL 验证。")


def install_dependencies(requirements, *, python=sys.executable, mode="auto", index_url=None,
                         proxy=None, ca_bundle=None, upgrade_pip=True, runner=run_process,
                         emit=report, environ=None):
    requirements = Path(requirements).resolve()
    if not requirements.is_file():
        raise InstallError("没有找到基础 requirements.txt，请完整解压项目后重试。")
    original = dict(os.environ if environ is None else environ)
    probe_env = dict(original, PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONIOENCODING="utf-8")
    try:
        probe = runner([python, "-m", "pip", "--version"], env=probe_env, timeout=15)
        if probe.returncode:
            probe = runner([python, "-m", "ensurepip", "--upgrade"], env=probe_env, timeout=60)
            if probe.returncode:
                raise InstallError("项目 Python 缺少可用的 pip，请修复 Python 的 pip/venv 组件。")
            probe = runner([python, "-m", "pip", "--version"], env=probe_env, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("无法启动项目 Python/pip，请检查 Python 安装。") from None
    version = re.search(r"pip (\d+)\.(\d+)", probe.stdout or "")
    if probe.returncode or not version:
        raise InstallError("无法识别项目的 pip 版本。")
    version = tuple(map(int, version.groups()))
    settings = pip_configuration(python, probe_env, runner)
    env = child_environment(original, settings, proxy=proxy, ca_bundle=ca_bundle)
    sources = selected_sources(mode, settings, original, index_url)
    emit("使用独立的单源安装配置；保留网络代理/CA，不修改系统 pip 源，不使用额外源或 trusted-host。")
    # Older Python 3.10+ bundled pip can opt into system certificate trust.
    truststore = (22, 2) <= version < (24, 2)
    if upgrade_pip and version < (24, 2):
        try:
            sources = pip_install(python, ["--upgrade", "pip"], sources, env, runner, emit,
                                  wall_timeout=60, truststore=truststore)
            # Modern pip enables system trust by default; old versions still
            # accept this opt-in flag if a private index only has an old pip.
        except InstallError:
            emit("pip 更新未完成，将继续用已有 pip 安装基础依赖，不因此直接中断。")
    pip_install(python, ["-r", str(requirements)], sources, env, runner, emit, truststore=truststore)
    try:
        checked = runner([python, "-m", "pip", "check"], env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        raise InstallError("依赖完整性检查无法完成。") from None
    if checked.returncode:
        raise InstallError("依赖存在缺失或版本冲突，请在项目环境运行 python -m pip check 检查。")
    emit("基础依赖安装及完整性检查通过。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--source", choices=["auto", "pypi", "tuna", "configured"], default="auto")
    parser.add_argument("--index-url")
    parser.add_argument("--proxy")
    parser.add_argument("--ca-bundle")
    parser.add_argument("--no-pip-upgrade", action="store_true")
    args = parser.parse_args()
    try:
        install_dependencies(args.requirements, mode=args.source, index_url=args.index_url, proxy=args.proxy,
                             ca_bundle=args.ca_bundle, upgrade_pip=not args.no_pip_upgrade)
    except InstallError as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
