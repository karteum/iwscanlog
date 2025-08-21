#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Adrien Demarez
Small tool to log Wifi APs and their center frequency and bandwidth from various sources (iwlist, iw, adb)
"""

import pandas as pd
import sqlite3
import numpy as np
import re
from time import time, sleep
import json
import argparse
import sys
import os

from signal import signal,SIGINT,SIG_IGN
import subprocess
from serial import Serial
from pynmeagps import NMEAReader
from pathlib import Path

def dbgprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

class DelayedKeyboardInterrupt:
    def __enter__(self):
        self.signal_received = False
        self.old_handler = signal(SIGINT, self.handler)

    def handler(self, sig, frame):
        self.signal_received = (sig, frame)

    def __exit__(self, type, value, traceback):
        signal(SIGINT, self.old_handler)
        if self.signal_received:
            self.old_handler(*self.signal_received)

def cmd(mycmd, mode=None): # FIXME: issue popen/subprocess iw/iwlist
    """Launches a subcommand while ignoring SIGINT"""
    if mode=="popen":
        res= os.popen(mycmd).read()
        #print(mycmd)
    else:
        res1 = subprocess.run(mycmd.split(), capture_output=True, text=True, preexec_fn=lambda: signal(SIGINT, SIG_IGN))
        res = res1.stdout
        #print(res)
    return res

def adb_gps():
    res = cmd("adb shell dumpsys location")
    if res=='':
        #raise Exception("ADB_unavailable")
        return None, None
    for line in res.splitlines():
        if line.startswith("      last location=Location[fused "):
            pos = line[35:line.index(" hAcc=")]
            break
    pos2 = pos.split(",")
    lon = float(pos2[0])
    lat= float(pos2[1])
    return lon, lat

def nmea_gps():
    with Serial('/dev/ttyUSB0', 4800, timeout=3) as stream:
        nmr = NMEAReader(stream)
        parsed_data = None
        while not(parsed_data is not None and hasattr(parsed_data,'lat') and hasattr(parsed_data,"lon")):
            raw_data, parsed_data = nmr.read()
            #print(parsed_data)
        return parsed_data.lon, parsed_data.lat

def adb_scan():
    #adb service list
    #https://gist.github.com/nstarke/615ca3603fdded8aee47fab6f4917826
    #https://android.stackexchange.com/questions/225260/termux-running-termux-via-adb-without-any-direct-interaction-with-the-device
    #run-as com.termux files/usr/bin/bash -lic 'export PATH=/data/data/com.termux/files/usr/bin:$PATH; export LD_PRELOAD=/data/data/com.termux/files/usr/lib/libtermux-exec.so; bash -i'
    # prerequisites: 1°/ a debug version of Termux and Termux-API (e.g. from Github releases, not from Play Store or F-Droid), and 2°/ pkg install termux-api
    res = cmd("adb shell run-as com.termux files/usr/bin/termux-wifi-scaninfo")
    return pd.DataFrame(json.loads(res))

def wifi_channel_plan(): # FIXME: alert when channel out of range
    df0 = pd.DataFrame({'channel': np.arange(1, 14+1)})
    df0['Fc'] = 2412 + (df0.channel-1) * 5
    df0['BW'] = 20
    df1 = pd.DataFrame({'channel': np.append(np.arange(32, 144+1, 4), np.arange(149, 177+1, 4))})
    df1['Fc'] = 5160 + (df1.channel - 32) * 5
    df1['BW'] = 20
    df2 = pd.DataFrame({'channel': np.append(np.arange(38, 142+1, 8), np.arange(151, 175+1, 8))})
    df2['Fc'] = 5190 + (df2.channel - 38) * 5
    df2['BW'] = 40
    df3 = pd.DataFrame({'channel': np.append(np.arange(42, 138+1, 16), np.arange(155, 171+1, 16))})
    df3['Fc'] = 5210 + (df3.channel - 42) * 5
    df3['BW'] = 80
    df4 = pd.DataFrame({'channel': np.append(np.arange(50, 114+1, 32), 163)})
    df4['Fc'] = 5250 + (df4.channel - 50) * 5
    df4['BW'] = 160
    df = pd.concat([df0, df1, df2, df3, df4])
    df = df[~np.logical_and(df.channel>=70, df.channel<=94)] # Nothing between 5350-5470 MHz
    df['fmin'] = df.Fc - df.BW/2
    df['fmax'] = df.Fc + df.BW/2
    df.set_index("channel", inplace=True)
    return df

def iw_scan(iface="wlo1"):
    # Preliminary requirement: sudo setcap 'CAP_NET_ADMIN=ep' /sbin/iw
    #sys.stderr.write('[') ; sys.stderr.flush()
    wlans = cmd(f'/sbin/iw {iface} scan flush') # /sbin/iw dev wlo1 scan flush ?
    #sys.stderr.write(']') ; sys.stderr.flush()
    return wlans

def parse_iw_scan(wlanstr):
    # FIXME: in a future version, use directly RTNETLINK since iw --help says "Do NOT screenscrape this tool, we don't consider its output stable"
    # Developed and tested on Debian Bookworm and Trixie
    # FIXME: how to get channel quality (not just signal level) ?
    wlans = []
    HTMODE = 0
    fplan = wifi_channel_plan()
    for line in wlanstr.splitlines():
        line = line.replace("\t", "").rstrip()
        if line.startswith("BSS ") and "(on " in line: #and (line.endswith(f'(on {iface})') or line.endswith("-- associated")):
            wlans.append({"MAC": line[4:21]}) #mac = line.replace("BSS ", "").replace(f'(on {iface})', '').replace(" -- associated","")
            wlans[-1]["ID"] = int(line[4:21].replace(':',''), 16)
        elif line.startswith("freq: "):
            wlans[-1]["freq_20"] = int(float(line[6:])) #.replace("freq: ", "")
        elif line.startswith("signal: "):
            wlans[-1]["Signal"] = int(float(line[8:-4])) #line.replace("signal: ", "").replace(" dBm", "")
        elif line.startswith("SSID: "):
            wlans[-1]["SSID"] = line[6:] #line.replace("SSID: ", "")
        elif line.startswith("DS Parameter set:"):
            wlans[-1]["DS Parameter set"] = int(line[26:]) #.replace("DS Parameter set: channel ", "")
        elif line.startswith("Country:"):
            tmp = line.replace("Country: ", "").replace("Environment: ", "|").split("|")
            wlans[-1]["Country"] = tmp[0]
            wlans[-1]["Environment"] = tmp[1]
        elif line.startswith("Channels "):
            wlans[-1]["Channels"] = line[9:] #.replace("Channels ", "")
        elif line == "HT operation:":
            HTMODE=1
        elif HTMODE==1 and (line.startswith( " * primary channel:") or line.startswith( " * secondary channel offset:") or line.startswith( " * STA channel width:")):
            line = line.replace(" * ", "").replace(": ",":")
            k,v = line.split(':')
            wlans[-1]["HT " + k] = v
        elif line == "VHT operation:":
            HTMODE=2
        elif HTMODE==2 and (line.startswith( " * channel width: ") or line.startswith( " * center freq segment")):
            line = line.replace(" * ", "").replace(": ",":")
            k,v = line.split(':')
            wlans[-1]["VHT " + k] = v
    for net in wlans:                
        BW = 20
        net["channel_20"] = fplan[fplan.Fc==net["freq_20"]].index[0]
        net["Fc"] = net["freq_20"]
        net2=net.copy()
        del net2["Signal"]
        net["dbg"] = str(net2)
        del net2
        if "HT primary channel" in net:
            HT0_channel = int(net["HT primary channel"])
            if HT0_channel in fplan.index:
                try: assert(net["channel_20"] == HT0_channel) #assert(fplan.loc[HT0_channel].Fc == net["freq_20"])
                except: dbgprint(f'assert(net["channel_20"] == HT0_channel) : {net}')
                del net["HT primary channel"]
                if "DS Parameter set" in net:
                    try: assert(HT0_channel == net["DS Parameter set"])
                    except: dbgprint(f'Error assert(HT0_channel == net["DS Parameter set"]) : {net}')
                    del net["DS Parameter set"]
                if net["HT STA channel width"] == "any":
                    BW = 40
                    freq = fplan.loc[HT0_channel].Fc + (10 if net["HT secondary channel offset"]=="above" else - 10)
                    net["freq_40"] = int(freq)
                    #if freq>5000: # FIXME: otherwise what ?
                    #    try: net["channel_40"] = fplan[fplan.Fc==freq].index[0]
                    #    except: dbgprint(f'Error fplan[fplan.Fc==freq].index[0] : {net}')
                    net["Fc"] = net["freq_40"]
                del net["HT secondary channel offset"], net["HT STA channel width"]
            else: dbgprint(f'Error fplan.loc[HT0_channel] : {net}')

        if "VHT channel width" in net and "VHT center freq segment 1" in net:
            BW_code = int(net["VHT channel width"][0])
            VHT_channel = int(net["VHT center freq segment 1"])
            if VHT_channel in fplan.index and VHT_channel>32 and BW_code>0:
                BW = 80 if BW_code==1 else 160
                chan = fplan.loc[VHT_channel]
                freq = chan.Fc
                if "VHT center freq segment 2" in net:
                    VHT_channel2 = int(net["VHT center freq segment 2"])
                    if VHT_channel2 in fplan.index: #implies > 0
                        chan2 = fplan.loc[VHT_channel2]
                        if chan2.BW==160:
                            freq = chan2.Fc
                            if BW<160:
                                #dbgprint(f"Error VHT channel width should be >1 (BW==160) : {net}")
                                BW = 160
                        elif chan2.BW==80 and chan2.Fc==freq+80:
                            freq += 40
                            if BW<160:
                                #dbgprint(f"Error VHT channel width should be >1 (BW==160) : {net}")
                                BW = 160
                        else:
                            dbgprint(f"Error chan2 not contiguously above chan1 (case not handled yet): {net}") # FIXME: not handled yet
                            net["Fc2_VHT"] = chan2.Fc
                            #net["dbg"] += " ___ chan2 not contiguously above chan1"
                    #else: dbgprint(f'Error fplan.loc[VHT_channel2] : {net}')
                net["freq_VHT"] = int(freq)
                #net["channel_VHT"] = fplan[fplan.Fc==freq].index[0]
                net["Fc"] = net["freq_VHT"]
            #else: dbgprint(f'Error fplan.loc[VHT_channel] : {net}') #if VHT_channel>=32:
            del net["VHT center freq segment 2"], net["VHT center freq segment 1"], net["VHT channel width"]
        net["chanbw"] = BW
        # if "freq_VHT" in net:
        #     net["Fc"] = net["freq_VHT"]
        #     #net["Channel"] = net["channel_VHT"]
        # elif "freq_40" in net and "channel_40" in net:
        #     net["Fc"] = net["freq_40"]
        #     #net["Channel"] = net["channel_40"]
        # else:
        #     net["Fc"] = net["freq_20"]
            #net["Channel"] = net["channel_20"]
    df = pd.DataFrame(wlans)
    if len(df)>0:
        df["fmin"] = df.Fc - df.chanbw//2
        df["fmax"] = df.Fc + df.chanbw//2
        df.set_index("ID", inplace=True)
    return df

def get_ubnt_wlans(username):
    athcmds = 'ifconfig ath0 down ; sleep 1 ; ifconfig ath0 up ; sleep 1 ; iwlist ath0 scan'
    mycmd = f'''ssh -oHostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedKeyTypes=ssh-rsa  -o ConnectTimeout=15 -i {Path.home()}/.ssh/id_rsa {username}@192.168.1.20 "{athcmds}"''' # -legacy ?
    wlans = cmd(mycmd, mode="popen")
    return wlans

def parse_iwlist_scan(wlans):
    wlans = re.sub('\n +', '\n', wlans)
    wlans = re.sub('\n.* - Address:', '\nAddress:', wlans)
    wlans = wlans.replace("Extra:", "").replace(" level", "").replace("=", ":").replace("(Channel", "\nChannel:") \
                 .replace(")", "").replace('"', "").replace(": ", ":").replace(" Mhz", "").replace(" dBm", "") \
                 .replace("  Signal", "\nSignal").replace("  Noise", "\nNoise")
    wlan_strlist = wlans.split('\n')
    wlan_nets = []
    wordlist = ("MAC", "Address", "ESSID", "Frequency", "Signal", "center1", "chanbw", "Channel") #"ieee_mode", "Quality", "Noise"
    for line in wlan_strlist:
        line = line.rstrip()
        if line.startswith('Address'):
            k,v = line.split(":", 1)
            wlan_nets.append({'MAC':v.lower()})
            v_int=int(v.replace(':',''), 16)
            wlan_nets[-1]["ID"] = v_int
        else:
            flag=False
            for w in wordlist:
                if line.startswith(w):
                    flag=True
                    break
            if flag==False: continue
            k,v = line.split(":", 1)
            if k in ('center1', 'chanbw', 'Signal', 'Channel'): #'Noise', 
                v = int(v)
            elif k=='Quality':
                v = int(v.replace('/94', ''))
            elif k=='Frequency':
                v = int(float(v.replace(" GHz", ""))*1000)
            wlan_nets[-1][k] = v
    #if len(wlan_nets)==0:
    #    return None
    df = pd.DataFrame(wlan_nets)
    if len(df) > 0:
        df.set_index("ID", inplace=True)
        if not "Channel" in df.columns:
            df["Channel"] = None
        df["fmin"] = df.center1 - df.chanbw//2
        df["fmax"] = df.center1 + df.chanbw//2
        df.rename(columns={"center1": "Fc", "ESSID": "SSID", "Frequency": "freq_20", "Channel": "channel_20"}, inplace=True)
        #df2 = df[['MAC', 'Fc', 'chanbw', 'fmin', 'fmax', 'Time', 'Signal', 'SSID', 'Country', 'Environment', 'Channels', 'channel_20', 'freq_20', 'freq_40', 'channel_40', 'freq_VHT', 'channel_VHT']].copy()
        #df.Channel = df.Channel.astype(pd.Int16Dtype())
    return df # wlans,wlan_nets,

def store(df, dbfilename="wlans.db", azimuth=None, mytime=None, table_suffix=""):
    if df is None or len(df)==0:
        return None, None
    if 'Time' in df.columns:
        df.rename(columns={"Time": "time"}, inplace=True)
    if 'time' in df.columns:
        t = df.time.min()
    else:
        t = int(time()) if mytime is None else mytime
        df["time"] = t
    if not 'azimuth' in df.columns: df['azimuth'] = azimuth
    df_static_fields = ["MAC", "SSID", "fmin", "fmax", "Fc", "chanbw", "interface"] #"ieee_mode", "center1", "Fc2_VHT", "channel_20", "dbg", 
    df_dyn_fields = ["Signal", "time", "lon", "lat", "azimuth"] # "Quality", "Noise",
    for field in df_static_fields+df_dyn_fields:
        if not field in df.columns:
            df[field] = None
    df1 = df[df_static_fields].drop_duplicates()
    df2 = df[df_dyn_fields]
    #df3 = pd.DataFrame({"time": [t], "lon": lon, "lat": lat, "azimuth": azimuth})

    conn = sqlite3.connect(dbfilename) # FIXME: with conn:
    try:
        df1_existing = pd.read_sql(f"select * from networks{table_suffix}", conn)
        for field in df_static_fields:
            if not field in df1_existing.columns:
                df1_existing[field] = None
        df1_existing.set_index('ID', inplace=True)
        #df1_final = pd.merge(df1_existing, df1, how='outer"')
        #df1_final = pd.concat([df1_existing, df1]).drop_duplicates(keep=False)
        cond = df1.index.isin(df1_existing.index)
        df1_final = df1.drop(df1[cond].index)
    except:
        df1_final = df1
        print("create table")
    df1_final.to_sql(f'networks{table_suffix}', conn, if_exists='append', dtype={'ID': 'INTEGER PRIMARY KEY'}) # replace is for whole table, not per record
    df2.to_sql(f'measurements{table_suffix}', conn, if_exists='append')
    #df3.to_sql("sessions", conn, if_exists='append')
    #df1_final.to_excel(dbfilename + ".xlsx")
    return df1, df2

# def mergedb(db_dest, db2, azimuth=None):
#     conn = sqlite3.connect(db2)
#     df1 = pd.read_sql("select * from networks", conn)
#     df1.set_index('ID', inplace=True)
#     df2 = pd.read_sql("select * from measurements", conn)
#     df2.set_index('ID', inplace=True)
#     df_final = df1.merge(df2, left_index=True, right_index=True)
#     store(df_final, db_dest, azimuth=azimuth)
    #try: df3 = pd.read_sql("select * from sessions", conn)
    #except: df3=None
    #if (df3 is None or not any(df3.azimuth)) and azimuth is not None:
    #    print("foo")
    #return df1, df2

def mergedb(db1, db2):
    print(f"Merging {db2} into {db1}")
    with sqlite3.connect(args.db) as conn:
        cur = conn.cursor()
        cur.executescript(f"""
             attach database '{db2}' as otherdb;
             insert or replace into networks select * from otherdb.networks;
             insert or replace into measurements select * from otherdb.measurements;
             insert or replace into drivepos select * from otherdb.drivepos;
         """)

class macoui(): # from https://standards-oui.ieee.org/
    def __init__(self, filename=None):
        if filename is None: filename="oui.txt"
        print("Initializing OUI")
        ouitxt = open(filename).read()
        regex = re.compile(r"([0-9A-F-]+)\s+\(hex\)\s+(.+?)\n", re.VERBOSE)
        matches = regex.findall(ouitxt)
        self.oui = {match[0]: match[1].strip() for match in matches}

    def getvendor(self, mac):
        prefix=mac[:8].upper().replace(':','-')
        return self.oui[prefix] if prefix in self.oui else None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--azimuth", "-a", help="Azimuth", default=None)
    parser.add_argument("--iw", "-i", help="Internal scan", action='store_true', default=False)
    parser.add_argument("--sshuser", "-s", help="SSH username for scanning through router", default=None)
    parser.add_argument("--adb", "-b", help="ADB scan through smartphone", action='store_true', default=False)
    parser.add_argument("--gps", "-g", help="GPS log through smartphone", action='store_true', default=False)
    parser.add_argument("--db", "-d", help="Log in SQLite DB", default=None)
    parser.add_argument("--print", "-p", help="Print on console", action='store_true', default=True)
    parser.add_argument("--num_iterations", "-n", help="Number of iterations", default=1<<24)
    parser.add_argument("--mergedb", "-m", help="Merge with other DB", default=None)
    args = parser.parse_args()
    n = args.num_iterations
    with DelayedKeyboardInterrupt():
        if args.mergedb is not None and args.db is not None:
            mergedb(args.db, args.mergedb)
            exit(0)

    while (n>0):
        with DelayedKeyboardInterrupt():
            if args.gps:
                lon,lat = adb_gps()
            if args.azimuth is not None:
                pass
            if args.iw:
                df_iw = parse_iw_scan(iw_scan())
                if args.db: store(df_iw, args.db, azimuth=args.azimuth)
            if args.sshuser is not None:
                df_ssh = parse_iwlist_scan(get_ubnt_wlans(args.sshuser))
                if args.db: store(df_ssh, args.db, azimuth=args.azimuth)
            if args.adb:
                df_adb = adb_scan()
                if args.db: store(df_adb, args.db, azimuth=args.azimuth)
            if args.print:
                #print('\033[2J')
                print('_______________________________\n')
                if args.iw: print(df_iw)
                if args.sshuser: print(df_ssh)
                if args.adb: print(df_adb)
        sleep(10)
        n -= 1
