<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.xai-personalize.server</string>

  <key>ProgramArguments</key>
  <array>
    <string>__INSTALL_DIR__/run.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>__INSTALL_DIR__</string>

  <!-- Start the dashboard server at login and keep it alive: if it crashes or
       is killed, launchd restarts it. This is why the page is always reachable
       at http://127.0.0.1:7873 without anyone holding a terminal open. -->
  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <!-- Don't thrash if the server exits immediately (e.g. bad config). -->
  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>StandardOutPath</key>
  <string>/tmp/xai-personalize-server.log</string>

  <key>StandardErrorPath</key>
  <string>/tmp/xai-personalize-server.err</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>__HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>DASHBOARD_PORT</key>
    <string>7873</string>
  </dict>
</dict>
</plist>
