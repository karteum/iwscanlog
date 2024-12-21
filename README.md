# iwscanlog
This tool implements some generic code to gather and log Wi-fi networks.
It does this without requiring monitor mode (e.g. it only listen to AP beacons, unlike more sophisticated tools such as [Kismet](https://www.kismetwireless.net)), and the primary goal was to be able to decode properly the channels/frequencies used (a lot of tools get it wrong. In my case I first wanted to be able to know which networks operate in DFS channels overlapping with weather radars for the purpose of interference troubleshootings).
More precisely, it can
* Parse the output of `/sbin/iw wlo1 scan` (even though iw --help says "_Do NOT screenscrape this tool, we don't consider its output stable_", it's working well enough at the moment. A further version may properly use RT NETLINK sockets)
* Parse the output of `iwlist ath0 scan` e.g. launched through ssh (iwlist is superseded by iw in modern distribution, but a lot of old router firmwares still use iwlist. Notice that depending on the firmware, the frequencies fmin/fmax from iwlist may be erroneous in some scenarios)
* Parse the output of `adb shell dumpsys location` to get longitude/latitude from an Android phone with ADB enabled
* parse the output of `adb shell run-as com.termux files/usr/bin/termux-wifi-scaninfo` to get Wi-fi networks from an Android phone with ADB enabled and a version of Termux with debug enabled
* Log this into an SQLite database (and merge several DB corresponding to different measurements), aggregating the network IDs and frequencies and logging separately the signal strength/position/azimuth/time of the measurement

## Warning: work-in-progress
This is still a work-in-progress. It fits several of my personal needs but it has not been tested in many different situations and it may crash in various scenarios.