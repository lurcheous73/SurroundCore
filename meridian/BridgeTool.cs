using System;
using System.Collections.Concurrent;
using System.Net.NetworkInformation;
using System.Threading;
using Sooloos;
using Sooloos.Broker;
using ClientZone = Sooloos.Zone;

internal static class BridgeTool {
    static Connection connection;
    static ZoneTracker tracker;

    sealed class OrderedContext : SynchronizationContext {
        readonly BlockingCollection<Action> queue = new BlockingCollection<Action>();
        public OrderedContext() {
            var thread = new Thread(delegate() {
                foreach (var work in queue.GetConsumingEnumerable()) work();
            });
            thread.IsBackground = true;
            thread.Start();
        }
        public override void Post(SendOrPostCallback callback, object state) {
            queue.Add(delegate { callback(state); });
        }
    }
    static void Initialize() {
        SynchronizationContext.SetSynchronizationContext(new OrderedContext());
        string serial = Environment.GetEnvironmentVariable("SURROUNDCORE_MERIDIAN_SERIAL");
        if (String.IsNullOrEmpty(serial)) {
            foreach (var nic in NetworkInterface.GetAllNetworkInterfaces()) {
                if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;
                var candidate = nic.GetPhysicalAddress().ToString();
                if (candidate.Length == 12 && candidate != "000000000000") {
                    serial = candidate;
                    break;
                }
            }
        }
        if (String.IsNullOrEmpty(serial) || serial.Length != 12)
            throw new InvalidOperationException("No usable Meridian client identity found.");
        Sooloos.SooloosProperty.CommandLine = new string[] { "--serialnumber=" + serial };
        Sooloos.Debug.ForceRealSerialNumber = false;
        Sooloos.Debug.Model = "SurroundCore";
    }

    static Sooid Id(string text, int type) {
        var bits = text.Split(':');
        return new Sooid((byte)type, Guid.Parse(bits[bits.Length - 1]));
    }
    static void Connect(string host) {
        Initialize();
        connection = new Connection(host);
        var ready = new ManualResetEvent(false);
        connection.ConnectionStatusChanged += delegate(IConnection c, ConnectionStatus status) {
            if (status.ToString() == "Connected") ready.Set();
        };
        connection.Connect();
        if (!ready.WaitOne(15000)) throw new TimeoutException("Could not connect to Meridian/Sooloos Core.");
        tracker = ZoneTracker.Instance;
        tracker.Init(connection, true);
        var deadline = DateTime.UtcNow.AddSeconds(12);
        while (tracker.Count == 0 && DateTime.UtcNow < deadline) Thread.Sleep(100);
        if (tracker.Count == 0) throw new InvalidOperationException("No Meridian/Sooloos zones found.");
    }

    static ClientZone FindZone(string id) {
        var target = Id(id, 22);
        for (int i = 0; i < tracker.Count; i++)
            if (tracker[i].ZoneId.Equals(target)) return tracker[i];
        throw new InvalidOperationException("Meridian zone not found.");
    }

    static string Clean(string value) {
        return (value ?? "").Replace("\t", " ").Replace("\r", " ").Replace("\n", " ");
    }
    static void Status() {
        Console.WriteLine("CMZONES\t" + tracker.Count);
        for (int i = 0; i < tracker.Count; i++) {
            var z = tracker[i];
            var state = z.Status == null ? "Unknown" : z.Status.State.ToString();
            var media = z.Status == null || z.Status.Media == null ? "" : z.Status.Media.Title;
            var subtitle = z.Status == null || z.Status.Media == null ? "" : z.Status.Media.Subtitle;
            Console.WriteLine("CMZONE\t" + z.ZoneId + "\t" + Clean(z.Name) + "\t" + state +
                "\t" + z.Volume + "\t" + z.VolumeMin + "\t" + z.VolumeMax + "\t" + z.IsMuted +
                "\t" + Clean(media) + "\t" + Clean(subtitle));
        }
    }

    static void Wake(ClientZone zone, int source) {
        var changed = new ManualResetEvent(false);
        ClientZone.MeridianZoneSourceChangedHandler handler = delegate(ClientZone z) {
            if (z.ZoneId.Equals(zone.ZoneId)) changed.Set();
        };
        zone.MeridianZoneSourceChanged += handler;
        try {
            zone.SetSource(source);
            if (!changed.WaitOne(5000)) throw new TimeoutException("Meridian source change was not confirmed.");
            Console.WriteLine("CMWAKE\t" + zone.ZoneId + "\t" + source + "\tconfirmed");
        } finally {
            zone.MeridianZoneSourceChanged -= handler;
        }
    }
    static void Transport(ClientZone z, string action) {
        switch ((action ?? "").ToLowerInvariant()) {
        case "play": z.TransportPlay(); break;
        case "pause": z.TransportPause(); break;
        case "playpause": z.TransportPlayPause(); break;
        case "next": z.TransportNext(); break;
        case "previous": z.TransportPrevious(); break;
        case "stop": z.TransportStop(); break;
        default: throw new ArgumentException("Unknown transport action.");
        }
    }

    static int Run(string[] args) {
        if (args.Length < 2) return 2;
        var command = args[0];
        Connect(args[1]);
        if (command == "status") Status();
        else if (command == "wake" && args.Length >= 3) Wake(FindZone(args[2]), args.Length >= 4 ? int.Parse(args[3]) : 2);
        else if (command == "transport" && args.Length == 4) Transport(FindZone(args[2]), args[3]);
        else if (command == "volume" && args.Length == 4) FindZone(args[2]).SetAudioVolume(int.Parse(args[3]));
        else if (command == "volume-relative" && args.Length == 4) FindZone(args[2]).ChangeAudioVolumeRelative(int.Parse(args[3]));
        else if (command == "mute" && args.Length == 4) FindZone(args[2]).SetAudioMute(bool.Parse(args[3]));
        else if (command == "pair" && args.Length == 4) FindZone(args[2]).SetPairWithUs(bool.Parse(args[3]));
        else return 2;
        if (command != "status" && command != "wake") Console.WriteLine("CMOK\t" + command);
        return 0;
    }
    public static int Main(string[] args) {
        try { return Run(args); }
        catch (Exception e) {
            Console.Error.WriteLine("CMERROR\t" + e.Message);
            return 1;
        }
        finally {
            if (connection != null) connection.Disconnect();
        }
    }
}
