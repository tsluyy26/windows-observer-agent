using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace WindowsObserverAgent
{
    // The host owns the only Job handle. Job membership, never a PID list,
    // authorizes termination. See docs/installation-windows.md for API references.
    public sealed class LiveProcessJob : IDisposable
    {
        private const uint KillOnJobClose = 0x2000;
        private const int BasicAccountingInformation = 1;
        private const int ExtendedLimitInformation = 9;
        private const uint CreateNoWindow = 0x08000000;
        private const uint ExtendedStartupInfoPresent = 0x00080000;
        private const uint CreateUnicodeEnvironment = 0x00000400;
        private const uint UseStdHandles = 0x100;
        private const int HandleList = 0x20002;
        private const int JobList = 0x2000D;
        private const uint QueryLimitedInformation = 0x1000;
        private const int ErrorInvalidParameter = 87;
        private const int ErrorInsufficientBuffer = 122;

        private readonly SafeFileHandle job;
        private bool confirmedEmpty;

        public LiveProcessJob()
        {
            job = CreateJobObjectW(IntPtr.Zero, null);
            try
            {
                if (job.IsInvalid) throw LastError();
                var limits = new ExtendedLimits { Basic = new BasicLimits { Flags = KillOnJobClose } };
                if (!SetInformationJobObject(job, ExtendedLimitInformation, ref limits,
                                             Marshal.SizeOf<ExtendedLimits>()))
                    throw LastError();
            }
            catch
            {
                job.Dispose();
                throw;
            }
        }

        public ProcessHandle Start(ProcessStartInfo start) => Start(start, start.Environment);

        public ProcessHandle Start(ProcessStartInfo start, IDictionary<string, string> environment)
        {
            if (environment == null) throw new ArgumentNullException(nameof(environment));
            if (confirmedEmpty || job.IsClosed)
                throw new InvalidOperationException("Cannot start a process in a closed Job.");
            if (!Path.IsPathFullyQualified(start.FileName) || !Path.IsPathFullyQualified(start.WorkingDirectory))
                throw new ArgumentException("Executable and working directory must be absolute.");

            AnonymousPipeServerStream output = null;
            AnonymousPipeServerStream errors = null;
            try
            {
                if (start.RedirectStandardOutput)
                    output = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
                if (start.RedirectStandardError)
                    errors = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
                ProcessHandle process = StartInJob(start, environment, output, errors);
                output = null; // The returned process owns the readers and pipes.
                errors = null;
                return process;
            }
            finally
            {
                output?.Dispose();
                errors?.Dispose();
            }
        }

        private ProcessHandle StartInJob(ProcessStartInfo start, IDictionary<string, string> environment,
                                         AnonymousPipeServerStream output, AnonymousPipeServerStream errors)
        {
            var security = new SecurityAttributes { Size = Marshal.SizeOf<SecurityAttributes>(), Inherit = true };
            using var sink = CreateFileW("NUL", 0xC0000000, 3, ref security, 3, 0, IntPtr.Zero);
            if (sink.IsInvalid) throw LastError();

            var setup = new StartupInfoEx
            {
                Info = new StartupInfo
                {
                    Size = Marshal.SizeOf<StartupInfoEx>(),
                    Flags = UseStdHandles,
                    Stdin = sink.DangerousGetHandle(),
                    Stdout = output?.ClientSafePipeHandle.DangerousGetHandle() ?? sink.DangerousGetHandle(),
                    Stderr = errors?.ClientSafePipeHandle.DangerousGetHandle() ?? sink.DangerousGetHandle(),
                }
            };

            // Only stdio handles may cross the process boundary. The Job is an
            // attribute of creation, not an inheritable handle or a later assignment.
            IntPtr[] handles = new[] { setup.Info.Stdin, setup.Info.Stdout, setup.Info.Stderr }.Distinct().ToArray();
            // Server launches pass a fresh environment without materializing start.Environment.
            string environmentBlock = string.Join("\0", environment
                .OrderBy(entry => entry.Key, StringComparer.OrdinalIgnoreCase)
                .Select(entry => entry.Key + "=" + entry.Value)) + "\0\0";
            var command = new StringBuilder(string.Join(" ",
                new[] { start.FileName }.Concat(start.ArgumentList).Select(QuoteArgument)));

            using var attributes = new AttributeList(2);
            attributes.Set(HandleList, handles);
            attributes.Set(JobList, new[] { job.DangerousGetHandle() });
            setup.Attributes = attributes.Pointer;
            IntPtr block = Marshal.StringToHGlobalUni(environmentBlock);
            ProcessInformation created;
            try
            {
                if (!CreateProcessW(start.FileName, command, IntPtr.Zero, IntPtr.Zero, true,
                                    CreateNoWindow | ExtendedStartupInfoPresent | CreateUnicodeEnvironment,
                                    block, start.WorkingDirectory, ref setup, out created))
                    throw LastError();
            }
            finally
            {
                for (int i = 0; i < environmentBlock.Length; i++)
                    Marshal.WriteInt16(block, i * sizeof(char), 0);
                Marshal.FreeHGlobal(block);
            }
            using var thread = new SafeFileHandle(created.Thread, true);
            var process = new SafeFileHandle(created.Process, true);
            try
            {
                output?.DisposeLocalCopyOfClientHandle();
                errors?.DisposeLocalCopyOfClientHandle();
                return new ProcessHandle(process, created.Pid,
                    output == null ? null : new StreamReader(output, start.StandardOutputEncoding ?? Encoding.UTF8),
                    errors == null ? null : new StreamReader(errors, start.StandardErrorEncoding ?? Encoding.UTF8));
            }
            catch
            {
                process.Dispose();
                // The caller still owns the Job and will stop it in finally.
                throw;
            }
        }

        public uint ActiveProcesses
        {
            get
            {
                if (!QueryInformationJobObject(job, BasicAccountingInformation, out Accounting state,
                                               Marshal.SizeOf<Accounting>(), IntPtr.Zero))
                    throw LastError();
                return state.ActiveProcesses;
            }
        }

        public bool ContainsProcess(uint pid, DateTime createdUtc)
        {
            using var process = OpenProcess(QueryLimitedInformation, false, pid);
            if (process.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                if (error == ErrorInvalidParameter) return false; // The PID no longer exists.
                throw new Win32Exception(error);
            }
            if (!IsProcessInJob(process, job, out bool member)) throw LastError();
            // CIM creation time can be rounded to microseconds.
            return member && Math.Abs((GetStartTime(process) - createdUtc).Ticks) < TimeSpan.TicksPerMillisecond;
        }

        public bool Stop(int milliseconds, ProcessHandle root)
        {
            if (confirmedEmpty) return true;
            var timer = Stopwatch.StartNew();
            if (ActiveProcesses != 0 && !TerminateJobObject(job, 1)) throw LastError();
            do
            {
                if (ActiveProcesses == 0 && (root == null || root.HasExited))
                {
                    confirmedEmpty = true;
                    return true;
                }
                if (timer.ElapsedMilliseconds >= milliseconds) return false;
                Thread.Sleep(20);
            } while (true);
        }

        public void Dispose() => job.Dispose();

        private static Win32Exception LastError() => new Win32Exception(Marshal.GetLastWin32Error());

        private static DateTime GetStartTime(SafeFileHandle process)
        {
            if (!GetProcessTimes(process, out long created, out _, out _, out _)) throw LastError();
            return DateTime.FromFileTimeUtc(created);
        }

        // MS C runtime argv escaping. Always quote, including an empty argument.
        private static string QuoteArgument(string argument)
        {
            var quoted = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in argument)
            {
                if (c == '\\')
                {
                    slashes++;
                    continue;
                }
                quoted.Append('\\', c == '"' ? 2 * slashes + 1 : slashes).Append(c);
                slashes = 0;
            }
            return quoted.Append('\\', 2 * slashes).Append('"').ToString();
        }

        public sealed class ProcessHandle : IDisposable
        {
            private readonly SafeFileHandle process;
            public uint Id { get; }
            public DateTime StartTime { get; }
            public StreamReader StandardOutput { get; }
            public StreamReader StandardError { get; }
            public bool HasExited => WaitForExit(0);

            internal ProcessHandle(SafeFileHandle handle, uint pid, StreamReader output, StreamReader errors)
            {
                process = handle;
                Id = pid;
                StandardOutput = output;
                StandardError = errors;
                StartTime = GetStartTime(handle);
            }

            public bool WaitForExit(uint milliseconds)
            {
                uint result = WaitForSingleObject(process, milliseconds);
                if (result == 0) return true;
                if (result == 258) return false;
                throw LastError();
            }

            public int ExitCode
            {
                get
                {
                    if (!HasExited) throw new InvalidOperationException("Process is still running.");
                    if (!GetExitCodeProcess(process, out int code)) throw LastError();
                    return code;
                }
            }

            public void Dispose()
            {
                process.Dispose();
                StandardOutput?.Dispose();
                StandardError?.Dispose();
            }
        }

        private sealed class AttributeList : IDisposable
        {
            private readonly System.Collections.Generic.List<IntPtr> values = new();
            public IntPtr Pointer { get; }

            public AttributeList(int count)
            {
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, count, 0, ref size);
                if (Marshal.GetLastWin32Error() != ErrorInsufficientBuffer) throw LastError();
                Pointer = Marshal.AllocHGlobal(size);
                if (!InitializeProcThreadAttributeList(Pointer, count, 0, ref size))
                {
                    Win32Exception error = LastError();
                    Marshal.FreeHGlobal(Pointer);
                    throw error;
                }
            }

            public void Set(int attribute, IntPtr[] data)
            {
                int size = IntPtr.Size * data.Length;
                IntPtr value = Marshal.AllocHGlobal(size);
                values.Add(value);
                Marshal.Copy(data, 0, value, data.Length);
                if (!UpdateProcThreadAttribute(Pointer, 0, (IntPtr)attribute, value, (IntPtr)size,
                                               IntPtr.Zero, IntPtr.Zero))
                    throw LastError();
            }

            public void Dispose()
            {
                DeleteProcThreadAttributeList(Pointer);
                foreach (IntPtr value in values) Marshal.FreeHGlobal(value);
                Marshal.FreeHGlobal(Pointer);
            }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicLimits
        {
            public long ProcessTime, JobTime;
            public uint Flags;
            public UIntPtr MinWorkingSet, MaxWorkingSet;
            public uint ActiveLimit;
            public UIntPtr Affinity;
            public uint Priority, Scheduling;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedLimits
        {
            public BasicLimits Basic;
            public ulong ReadCount, WriteCount, OtherCount, ReadBytes, WriteBytes, OtherBytes;
            public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct Accounting
        {
            public long UserTime, KernelTime, PeriodUserTime, PeriodKernelTime;
            public uint PageFaults, TotalProcesses, ActiveProcesses, TerminatedProcesses;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct SecurityAttributes
        {
            public int Size;
            public IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] public bool Inherit;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct StartupInfo
        {
            public int Size;
            public IntPtr Reserved, Desktop, Title;
            public uint X, Y, XSize, YSize, XChars, YChars, Fill, Flags;
            public ushort ShowWindow, ReservedSize;
            public IntPtr ReservedBytes, Stdin, Stdout, Stderr;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct StartupInfoEx
        {
            public StartupInfo Info;
            public IntPtr Attributes;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation
        {
            public IntPtr Process, Thread;
            public uint Pid, Tid;
        }

        [DllImport("kernel32.dll", ExactSpelling = true, CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateJobObjectW(IntPtr security, string name);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool SetInformationJobObject(SafeFileHandle handle, int type, ref ExtendedLimits info, int size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool QueryInformationJobObject(SafeFileHandle handle, int type, out Accounting info, int size, IntPtr used);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool TerminateJobObject(SafeFileHandle handle, uint code);

        [DllImport("kernel32.dll", ExactSpelling = true, CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string name, uint access, uint share,
            ref SecurityAttributes security, uint disposition, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, uint flags, ref IntPtr size);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute,
            IntPtr value, IntPtr size, IntPtr previous, IntPtr returnSize);

        [DllImport("kernel32.dll")]
        private static extern void DeleteProcThreadAttributeList(IntPtr list);

        [DllImport("kernel32.dll", ExactSpelling = true, CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcessW(string application, StringBuilder command, IntPtr processSecurity,
            IntPtr threadSecurity, bool inherit, uint flags, IntPtr environment, string cwd,
            ref StartupInfoEx startup, out ProcessInformation process);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern SafeFileHandle OpenProcess(uint access, bool inherit, uint pid);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool IsProcessInJob(SafeFileHandle process, SafeFileHandle job, out bool member);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetProcessTimes(SafeFileHandle process, out long created, out long exited,
            out long kernel, out long user);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(SafeFileHandle process, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetExitCodeProcess(SafeFileHandle process, out int code);
    }
}
