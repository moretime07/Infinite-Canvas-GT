// Build with the Windows .NET Framework compiler. No account or API key is
// embedded. The Python child runs from the same movable distribution folder.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Management;
using System.Net;
using System.Net.NetworkInformation;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Program
{
    internal static string Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
    internal static string Python { get { return Path.Combine(Root, "python", "python.exe"); } }
    internal static string MainFile { get { return Path.Combine(Root, "main.py"); } }
    internal static string Bootstrap { get { return Path.Combine(Root, "tools", "portable", "backend.py"); } }
    internal static string LogFile { get { return Path.Combine(Root, "data", "portable-launcher.log"); } }
    internal static int Port = 3000;
    private static readonly object LogLock = new object();
    internal static string Url { get { return "http://127.0.0.1:" + Port + "/"; } }

    [STAThread]
    private static int Main(string[] args)
    {
        bool selfTest = Array.IndexOf(args, "--self-test") >= 0;
        int index = Array.IndexOf(args, "--port");
        if (index >= 0 && (index + 1 >= args.Length || !int.TryParse(args[index + 1], out Port) || Port < 1024 || Port > 65535))
            return 2;
        string key;
        using (var hash = SHA256.Create())
            key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Root.ToLowerInvariant() + Port))).Replace("-", "");
        bool created;
        using (var mutex = new Mutex(true, "Local\\InfiniteCanvasGT-" + key, out created))
        {
            if (!created)
            {
                if (!selfTest) MessageBox.Show("此文件夹的启动器已经打开，请切回原窗口。", "无限画布");
                return 0;
            }
            try
            {
                if (selfTest)
                {
                    StartBackend();
                    if (!WaitUntilReady(60)) return 3;
                    if (Array.IndexOf(args, "--restart-test") >= 0 && !RestartSelfTest()) return 4;
                    Log("SELF_TEST_OK");
                    return 0;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new LauncherForm());
                return 0;
            }
            catch (Exception error)
            {
                // Do not display exception strings with process arguments,
                // environment variables or arbitrary upstream URLs.
                Log("Launcher failure: " + error.GetType().Name);
                if (!selfTest) MessageBox.Show("启动失败。请确认完整解压到可写目录，检查端口是否占用，并查看本机日志。", "无限画布");
                return 1;
            }
            finally
            {
                if (selfTest) StopBackend();
            }
        }
    }

    internal static void Log(string value)
    {
        try
        {
            lock (LogLock)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
                File.AppendAllText(LogFile, DateTime.Now.ToString("s") + " " + value + Environment.NewLine, Encoding.UTF8);
            }
        }
        catch (IOException) { }
    }

    internal static List<int> OwnBackends()
    {
        var found = new List<int>();
        using (var searcher = new ManagementObjectSearcher("SELECT ProcessId, ExecutablePath, CommandLine FROM Win32_Process WHERE Name='python.exe'"))
        using (var results = searcher.Get())
            foreach (ManagementObject row in results)
                using (row)
                {
                    var executable = row["ExecutablePath"] as string;
                    var command = row["CommandLine"] as string;
                    if (String.Equals(executable, Python, StringComparison.OrdinalIgnoreCase) && IsBackendCommand(command))
                        found.Add(Convert.ToInt32(row["ProcessId"]));
                }
        return found;
    }

    [DllImport("shell32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern IntPtr CommandLineToArgvW(string command, out int count);
    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);

    private static bool IsBackendCommand(string command)
    {
        if (String.IsNullOrEmpty(command)) return false;
        int count;
        IntPtr values = CommandLineToArgvW(command, out count);
        if (values == IntPtr.Zero) return false;
        try
        {
            for (int i = 1; i < count; ++i)
            {
                string value = Marshal.PtrToStringUni(Marshal.ReadIntPtr(values, i * IntPtr.Size));
                if (String.Equals(value, MainFile, StringComparison.OrdinalIgnoreCase) ||
                    String.Equals(value, Bootstrap, StringComparison.OrdinalIgnoreCase)) return true;
            }
            return false;
        }
        finally { LocalFree(values); }
    }

    internal static void StartBackend()
    {
        if (!File.Exists(Python) || !File.Exists(MainFile) || !File.Exists(Bootstrap) || !Directory.Exists(Path.Combine(Root, "static")))
            throw new FileNotFoundException("Incomplete distribution");
        if (OwnBackends().Count > 0) return;
        foreach (var endpoint in IPGlobalProperties.GetIPGlobalProperties().GetActiveTcpListeners())
            if (endpoint.Port == Port) throw new InvalidOperationException("PORT_IN_USE");
        Directory.CreateDirectory(Path.Combine(Root, "data"));
        var info = new ProcessStartInfo(Python, "-u -B \"" + Bootstrap + "\"");
        info.WorkingDirectory = Root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.EnvironmentVariables.Remove("PYTHONHOME");
        info.EnvironmentVariables.Remove("PYTHONPATH");
        info.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        info.EnvironmentVariables["PYTHONUTF8"] = "1";
        info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        info.EnvironmentVariables["INFINITE_CANVAS_PORTABLE"] = "1";
        info.EnvironmentVariables["INFINITE_CANVAS_PORT"] = Port.ToString();
        info.EnvironmentVariables["PATH"] = Path.Combine(Root, "bin", "ffmpeg", "bin") + ";" +
            Path.Combine(Root, "bin", "git", "cmd") + ";" + Path.Combine(Root, "python") + ";" +
            Path.Combine(Root, "python", "Scripts") + ";" + Environment.GetEnvironmentVariable("PATH");
        var process = new Process();
        process.StartInfo = info;
        if (!process.Start()) throw new InvalidOperationException("Could not start Python");
        Log("Started bundled backend.");
    }

    internal static string Status()
    {
        try
        {
            var request = (HttpWebRequest)WebRequest.Create(Url + "api/system/status");
            request.Proxy = null;
            request.Timeout = 1500;
            using (var response = (HttpWebResponse)request.GetResponse())
            using (var reader = new StreamReader(response.GetResponseStream()))
                return response.StatusCode == HttpStatusCode.OK ? reader.ReadToEnd() : null;
        }
        catch (WebException) { return null; }
    }

    internal static bool Ready() { string body = Status(); return body != null && body.Contains("instance_id"); }

    private static bool RestartSelfTest()
    {
        string before = Status();
        var request = (HttpWebRequest)WebRequest.Create(Url + "api/system/restart");
        request.Proxy = null; request.Timeout = 5000; request.Method = "POST"; request.ContentLength = 0;
        request.Headers["Origin"] = Url.TrimEnd('/');
        request.Headers["Sec-Fetch-Site"] = "same-origin";
        request.Headers["X-Infinite-Canvas-Restart"] = "1";
        using (var response = request.GetResponse()) { }
        DateTime deadline = DateTime.UtcNow.AddSeconds(60);
        while (DateTime.UtcNow < deadline)
        {
            string after = Status();
            if (after != null && after.Contains("instance_id") && after != before && OwnBackends().Count > 0)
            { Log("RESTART_TEST_OK"); return true; }
            Thread.Sleep(500);
        }
        return false;
    }

    internal static bool WaitUntilReady(int seconds)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (DateTime.UtcNow < deadline)
        {
            if (Ready()) return true;
            Thread.Sleep(500);
        }
        return false;
    }

    internal static void StopBackend()
    {
        // Re-query exact executable + parsed script argument so web-triggered
        // restarts are supported without ever killing another installation.
        foreach (int pid in OwnBackends())
            try
            {
                using (var process = Process.GetProcessById(pid))
                {
                    if (!String.Equals(process.MainModule.FileName, Python, StringComparison.OrdinalIgnoreCase)) continue;
                    process.Kill();
                    process.WaitForExit(5000);
                }
            }
            catch (ArgumentException) { }
            catch (InvalidOperationException) { }
    }

    internal static void OpenBrowser() { Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true }); }
}

internal class LauncherForm : Form
{
    private readonly Label status = new Label();
    private readonly Button start = new Button();
    private bool busy;

    internal LauncherForm()
    {
        Text = "无限画布 · 免安装启动器";
        ClientSize = new Size(560, 290);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        Font = new Font("Microsoft YaHei UI", 10);
        var title = new Label { Text = "Infinite Canvas GT", Location = new Point(24, 20), Size = new Size(510, 36), Font = new Font("Segoe UI", 18, FontStyle.Bold) };
        var intro = new Label { Text = "运行环境已内置，无需先安装 Python。\r\n在网页 API 设置中填写你自己的 Key；个人数据保存在本文件夹。", Location = new Point(24, 68), Size = new Size(510, 52) };
        status.Text = "准备启动（仅本机访问）…";
        status.Location = new Point(24, 132);
        status.Size = new Size(510, 44);
        start.Text = "启动 / 打开画布"; start.Location = new Point(24, 188); start.Size = new Size(170, 40);
        start.Click += async delegate { await StartAsync(); };
        var stop = new Button { Text = "停止此后台", Location = new Point(205, 188), Size = new Size(150, 40) };
        stop.Click += delegate {
            if (MessageBox.Show("停止会中断正在进行的生成任务，确定吗？", Text, MessageBoxButtons.YesNo) == DialogResult.Yes)
            { Program.StopBackend(); status.Text = "后台已停止。"; }
        };
        var logs = new Button { Text = "查看本机日志", Location = new Point(366, 188), Size = new Size(170, 40) };
        logs.Click += delegate {
            string backendLog = Path.Combine(Program.Root, "data", "portable-backend.log");
            string selectedLog = File.Exists(backendLog) ? backendLog : Program.LogFile;
            if (File.Exists(selectedLog)) Process.Start("notepad.exe", "\"" + selectedLog + "\"");
        };
        var note = new Label { Text = "请保留完整配套文件夹。GPU 白模/骨骼需要兼容的 NVIDIA 显卡及驱动。", Location = new Point(24, 246), Size = new Size(512, 30), Font = new Font("Microsoft YaHei UI", 9) };
        Controls.AddRange(new Control[] { title, intro, status, start, stop, logs, note });
        Shown += async delegate { await StartAsync(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (busy) { e.Cancel = true; return; }
            if (Program.OwnBackends().Count == 0) return;
            var answer = MessageBox.Show("是否同时停止此画布后台？\r\n是：停止任务并退出。否：后台继续运行。", Text, MessageBoxButtons.YesNoCancel);
            if (answer == DialogResult.Cancel) e.Cancel = true;
            else if (answer == DialogResult.Yes) Program.StopBackend();
        };
    }

    private async Task StartAsync()
    {
        if (busy) return;
        busy = true; start.Enabled = false; status.Text = "正在启动内置环境…";
        try
        {
            await Task.Run(delegate { Program.StartBackend(); });
            bool ready = await Task.Run(delegate { return Program.WaitUntilReady(60); });
            if (!ready) throw new TimeoutException();
            status.Text = "已运行：" + Program.Url + "\r\n关闭网页不会停止后台；可用下方按钮停止。";
            Program.OpenBrowser();
        }
        catch (InvalidOperationException)
        { status.Text = "端口已被占用。请先停止旧版后台，再点启动；不会强制结束其他程序。"; }
        catch (Exception error)
        { status.Text = "启动未完成，请查看本机日志，确认完整解压且目录可写。"; Program.Log("Start failure: " + error.GetType().Name); }
        finally { busy = false; start.Enabled = true; }
    }
}
