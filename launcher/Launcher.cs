// Stranger's Wrath Beta - launcher
//
// A small front end for the May-2004 Xbox beta running on our Cxbx-Reloaded fork.
// It exists so the thing can be double-clicked and played without anyone editing an
// emulator config: pick a resolution, press Play.
//
// What it actually does:
//   * writes [video] RenderResolution / VideoResolution / FullScreen into the
//     runtime's settings.ini, preserving every other key (the emulator reads a
//     dozen sections at start-up and CRASHES if any are missing, so this rewrites
//     values in place rather than regenerating the file)
//   * starts runtime\cxbx.exe with the game image
//
// RenderResolution is the internal upscale factor, not a window size. The Xbox
// renders 640x480; a factor of 3 renders internally at 1920x1440 and downsamples,
// which is what actually makes it look sharp. The game is 4:3, so a 16:9 fullscreen
// mode is pillarboxed - that is correct, not a bug. Stretching it would distort the
// image, and this build has no widescreen support to enable.
//
// Build:
//   csc /target:winexe /out:"Stranger's Wrath Beta.exe" /win32icon:stranger.ico
//       /r:System.Windows.Forms.dll /r:System.Drawing.dll Launcher.cs

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

static class Program
{
    // label, internal render scale, fullscreen, fullscreen mode string
    class Mode
    {
        public string Label;
        public int Scale;
        public bool FullScreen;
        public string VideoResolution;
        public Mode(string l, int s, bool f, string v) { Label = l; Scale = s; FullScreen = f; VideoResolution = v; }
        public override string ToString() { return Label; }
    }

    static readonly Mode[] Modes = new Mode[]
    {
        new Mode("640 x 480  -  original Xbox output",      1, false, ""),
        new Mode("1280 x 960  -  2x, windowed",             2, false, ""),
        new Mode("1920 x 1440  -  3x, windowed",            3, false, ""),
        new Mode("2560 x 1920  -  4x, windowed (demanding)",4, false, ""),
        new Mode("1920 x 1080  -  fullscreen 1080p",        3, true,  "1920 x 1080 32bit x8r8g8b8 (60 hz)"),
    };

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        string root = AppDomain.CurrentDomain.BaseDirectory;
        string runtime = Path.Combine(root, "runtime");
        // cxbxr-ldr.exe with /load runs the emulator HEADLESS - no GUI shell, no menu
        // bar, no settings dialogs, just the render window. cxbx.exe is the emulator's
        // own front end and refuses /load outright ("Emulation must be launched from
        // cxbxr-ldr.exe!"), so going through it is what was putting the Cxbx window
        // and menus on screen. Skipping it also means the GUI executable does not
        // need to ship at all.
        string emulator = Path.Combine(runtime, "cxbxr-ldr.exe");
        string settings = Path.Combine(runtime, "settings.ini");
        string xbe = LocateGame(root);

        if (!File.Exists(emulator))
        {
            MessageBox.Show("runtime\\cxbxr-ldr.exe is missing.\n\nThis folder is incomplete - re-create it with make_oddbeta.ps1.",
                "Stranger's Wrath Beta", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        if (xbe == null)
        {
            xbe = AskForGameFolder(root);
            if (xbe == null) return;   // user cancelled
        }

        var form = new Form();
        form.Text = "Stranger's Wrath Beta";
        form.FormBorderStyle = FormBorderStyle.FixedDialog;
        form.MaximizeBox = false;
        form.MinimizeBox = false;
        form.StartPosition = FormStartPosition.CenterScreen;
        form.ClientSize = new Size(420, 190);
        try { form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

        var title = new Label();
        title.Text = "Oddworld: Stranger's Wrath";
        title.Font = new Font(form.Font.FontFamily, 12f, FontStyle.Bold);
        title.SetBounds(16, 14, 388, 24);
        form.Controls.Add(title);

        var subtitle = new Label();
        subtitle.Text = "May 2004 Xbox development build";
        subtitle.ForeColor = SystemColors.GrayText;
        subtitle.SetBounds(18, 38, 388, 18);
        form.Controls.Add(subtitle);

        var label = new Label();
        label.Text = "Resolution";
        label.SetBounds(18, 74, 80, 20);
        form.Controls.Add(label);

        var combo = new ComboBox();
        combo.DropDownStyle = ComboBoxStyle.DropDownList;
        combo.SetBounds(18, 94, 384, 24);
        foreach (var m in Modes) combo.Items.Add(m);
        combo.SelectedIndex = LoadSavedIndex(settings);
        form.Controls.Add(combo);

        var credit = new Label();
        credit.Text = "PC port by JohnsonMichaels";
        credit.ForeColor = SystemColors.GrayText;
        credit.SetBounds(18, 148, 200, 18);
        form.Controls.Add(credit);

        var play = new Button();
        play.Text = "Play";
        play.SetBounds(302, 138, 100, 32);
        form.Controls.Add(play);
        form.AcceptButton = play;

        var quit = new Button();
        quit.Text = "Cancel";
        quit.SetBounds(194, 138, 100, 32);
        form.Controls.Add(quit);
        quit.Click += delegate { form.Close(); };
        form.CancelButton = quit;

        play.Click += delegate
        {
            var m = (Mode)combo.SelectedItem;
            try
            {
                ApplySettings(settings, m);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Could not write the emulator settings:\n\n" + ex.Message,
                    "Stranger's Wrath Beta", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            var psi = new ProcessStartInfo(emulator, "/load \"" + xbe + "\"");
            psi.WorkingDirectory = runtime;
            try { Process.Start(psi); }
            catch (Exception ex)
            {
                MessageBox.Show("Could not start the emulator:\n\n" + ex.Message,
                    "Stranger's Wrath Beta", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            form.Close();
        };

        Application.Run(form);
    }

    // Game\default.xbe beside the launcher, or a path written in game.txt. The
    // second form exists because the packager can be pointed at an existing
    // extraction rather than copying 2 GB of game data again.
    static string LocateGame(string root)
    {
        string local = Path.Combine(root, Path.Combine("Game", "default.xbe"));
        if (File.Exists(local)) return local;

        string pointer = Path.Combine(root, "game.txt");
        if (File.Exists(pointer))
        {
            foreach (string raw in File.ReadAllLines(pointer))
            {
                string line = raw.Trim();
                if (line.Length == 0 || line.StartsWith("#")) continue;
                if (File.Exists(line)) return line;
            }
        }
        return null;
    }

    // First run on someone else's machine: the game data is not shipped, so ask where
    // their own copy of the beta is and remember the answer in game.txt.
    //
    // The archive lays out as  <root>\data\  plus one folder per build
    // (Debug, Release, Final). The emulator needs the executable to sit BESIDE data\,
    // because the Xbox reads from D: which maps to the folder holding the image - so
    // if there is no default.xbe at the root, one is copied up from Final.
    //
    // Final specifically, not Debug: Debug is dated 2004-04-29 while all the data is
    // from 2004-05-22. Running the April build against May data makes its audio init
    // read a garbage lip-sync count and try to allocate 2 GB, which dies on a screen
    // it cannot even draw. Picking the wrong one here would look like a broken build.
    static string AskForGameFolder(string root)
    {
        MessageBox.Show(
            "Select the folder containing the 2004 beta.\n\n" +
            "It is the folder with a 'data' folder inside it (and usually Debug, Release and Final folders).",
            "Stranger's Wrath Beta", MessageBoxButtons.OK, MessageBoxIcon.Information);

        while (true)
        {
            string folder;
            using (var dlg = new FolderBrowserDialog())
            {
                dlg.Description = "Where is the beta?";
                dlg.ShowNewFolderButton = false;
                if (dlg.ShowDialog() != DialogResult.OK) return null;
                folder = dlg.SelectedPath;
            }

            // Accept being pointed at a build sub-folder by mistake.
            if (!Directory.Exists(Path.Combine(folder, "data")))
            {
                string parent = Path.GetDirectoryName(folder);
                if (parent != null && Directory.Exists(Path.Combine(parent, "data"))) folder = parent;
            }

            if (!Directory.Exists(Path.Combine(folder, "data")))
            {
                if (MessageBox.Show("That folder has no 'data' folder in it.\n\nTry again?",
                        "Stranger's Wrath Beta", MessageBoxButtons.RetryCancel,
                        MessageBoxIcon.Warning) != DialogResult.Retry) return null;
                continue;
            }

            string target = Path.Combine(folder, "default.xbe");
            if (!File.Exists(target))
            {
                string final = Path.Combine(folder, Path.Combine("Final", "SteefFinal.xbe"));
                if (!File.Exists(final))
                {
                    if (MessageBox.Show("Found the data, but not Final\\SteefFinal.xbe.\n\n" +
                            "That folder does not look like a complete copy of the beta.\n\nTry again?",
                            "Stranger's Wrath Beta", MessageBoxButtons.RetryCancel,
                            MessageBoxIcon.Warning) != DialogResult.Retry) return null;
                    continue;
                }
                try { File.Copy(final, target, false); }
                catch (Exception ex)
                {
                    MessageBox.Show("Could not prepare the game image:\n\n" + ex.Message,
                        "Stranger's Wrath Beta", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return null;
                }
            }

            try
            {
                File.WriteAllLines(Path.Combine(root, "game.txt"),
                    new string[] { "# Location of the beta. Delete this file to be asked again.", target },
                    Encoding.ASCII);
            }
            catch { /* not fatal - it will just ask again next time */ }

            return target;
        }
    }

    static int LoadSavedIndex(string settings)
    {
        try
        {
            if (!File.Exists(settings)) return 1;
            bool full = false;
            int scale = 1;
            foreach (string raw in File.ReadAllLines(settings))
            {
                string line = raw.Trim();
                if (line.StartsWith("RenderResolution")) int.TryParse(Value(line), out scale);
                else if (line.StartsWith("FullScreen")) full = Value(line).ToLowerInvariant() == "true";
            }
            for (int i = 0; i < Modes.Length; i++)
                if (Modes[i].Scale == scale && Modes[i].FullScreen == full) return i;
        }
        catch { }
        return 1; // 2x windowed is a sane default on a modern display
    }

    static string Value(string line)
    {
        int eq = line.IndexOf('=');
        return eq < 0 ? "" : line.Substring(eq + 1).Trim();
    }

    // Rewrite only the three keys we own, in place. Deliberately NOT a regenerated
    // file: settings.ini carries [audio], [network], [input-port-*], [input-profile-*],
    // [overlay] and [hack] sections that the emulator reads at start-up, and a
    // hand-written minimal file crashed it on launch once already.
    static void ApplySettings(string settings, Mode m)
    {
        if (!File.Exists(settings))
            throw new FileNotFoundException("runtime\\settings.ini is missing");

        string[] lines = File.ReadAllLines(settings);
        var outLines = new List<string>(lines.Length);
        string section = "";
        bool wroteScale = false, wroteRes = false, wroteFull = false;

        foreach (string raw in lines)
        {
            string trimmed = raw.Trim();

            // Closing the [video] section - add anything that was absent.
            if (trimmed.StartsWith("[") && section == "video")
            {
                if (!wroteScale) { outLines.Add("RenderResolution = " + m.Scale); wroteScale = true; }
                if (!wroteRes) { outLines.Add("VideoResolution = " + m.VideoResolution); wroteRes = true; }
                if (!wroteFull) { outLines.Add("FullScreen = " + (m.FullScreen ? "true" : "false")); wroteFull = true; }
            }

            if (trimmed.StartsWith("["))
                section = trimmed.Trim('[', ']').ToLowerInvariant();

            if (section == "video")
            {
                if (trimmed.StartsWith("RenderResolution")) { outLines.Add("RenderResolution = " + m.Scale); wroteScale = true; continue; }
                if (trimmed.StartsWith("VideoResolution")) { outLines.Add("VideoResolution = " + m.VideoResolution); wroteRes = true; continue; }
                if (trimmed.StartsWith("FullScreen")) { outLines.Add("FullScreen = " + (m.FullScreen ? "true" : "false")); wroteFull = true; continue; }
            }

            outLines.Add(raw);
        }

        // [video] was the last section in the file.
        if (section == "video")
        {
            if (!wroteScale) outLines.Add("RenderResolution = " + m.Scale);
            if (!wroteRes) outLines.Add("VideoResolution = " + m.VideoResolution);
            if (!wroteFull) outLines.Add("FullScreen = " + (m.FullScreen ? "true" : "false"));
        }

        File.WriteAllLines(settings, outLines.ToArray(), Encoding.ASCII);
    }
}
