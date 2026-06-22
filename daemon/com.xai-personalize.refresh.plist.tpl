<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.xai-personalize.refresh</string>

  <key>ProgramArguments</key>
  <array>
    <string>__INSTALL_DIR__/daemon/refresh.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>__INSTALL_DIR__</string>

  <key>RunAtLoad</key>
  <false/>

  <key>StartCalendarInterval</key>
  <array>
    <dict>
      <key>Hour</key>
      <integer>8</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
    <dict>
      <key>Hour</key>
      <integer>20</integer>
      <key>Minute</key>
      <integer>0</integer>
    </dict>
  </array>

  <key>StandardOutPath</key>
  <string>/tmp/xai-personalize-refresh.log</string>

  <key>StandardErrorPath</key>
  <string>/tmp/xai-personalize-refresh.err</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>__HOME__/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>DASHBOARD_PORT</key>
    <string>7873</string>
  </dict>
</dict>
</plist>
