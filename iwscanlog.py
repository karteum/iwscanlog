#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@author: Adrien Demarez
Small tool to log Wifi APs and their center frequency and bandwidth from various sources (iwlist, iw, adb)
"""

import pandas as pd
import geopandas as gpd
import sqlite3
import numpy as np
import sys,os
import re
from time import time, sleep
import json
import argparse

def adb_gps():
    res = os.popen("adb shell dumpsys location").read()
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

def geodf(df, lon=None, lat=None):
    if df is None: return None
    if lon is None or lat is None:
        lon,lat = adb_gps()
    df['lon'] = lon ; df['lat'] = lat
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.lon, df.lat), crs=4326)

def adb_scan():
    #adb service list
    #https://gist.github.com/nstarke/615ca3603fdded8aee47fab6f4917826
    #https://android.stackexchange.com/questions/225260/termux-running-termux-via-adb-without-any-direct-interaction-with-the-device
    #run-as com.termux files/usr/bin/bash -lic 'export PATH=/data/data/com.termux/files/usr/bin:$PATH; export LD_PRELOAD=/data/data/com.termux/files/usr/lib/libtermux-exec.so; bash -i'
    # prerequisites: 1°/ a debug version of Termux and Termux-API (e.g. from Github releases, not from Play Store or F-Droid), and 2°/ pkg install termux-api
    res = os.popen("adb shell run-as com.termux files/usr/bin/termux-wifi-scaninfo").read()
    return pd.DataFrame(json.loads(res))

def wifi_channel_plan():
    df0 = pd.DataFrame({'channel': np.arange(1, 14+1)})
    df0['Fc'] = 2412 + (df0.channel-1) * 5
    df0['BW'] = 20
    df1 = pd.DataFrame({'channel': np.arange(32, 144+1, 4)})
    df1['Fc'] = 5160 + (df1.channel - 32) * 5
    df1['BW'] = 20
    df2 = pd.DataFrame({'channel': np.arange(38, 142+1, 8)})
    df2['Fc'] = 5190 + (df2.channel - 38) * 5
    df2['BW'] = 40
    df3 = pd.DataFrame({'channel': np.arange(42, 138+1, 16)})
    df3['Fc'] = 5210 + (df3.channel - 42) * 5
    df3['BW'] = 80
    df4 = pd.DataFrame({'channel': np.arange(50, 114+1, 32)})
    df4['Fc'] = 5250 + (df4.channel - 50) * 5
    df4['BW'] = 160
    df = pd.concat([df0, df1, df2, df3, df4])
    df = df[~np.logical_and(df.channel>=70, df.channel<=94)] # Nothing between 5350-5470 MHz
    df['fmin'] = df.Fc - df.BW/2
    df['fmax'] = df.Fc + df.BW/2
    df.set_index("channel", inplace=True)
    return df

def iw_scan(iface="wlo1"):
    # Preliminary requirement: sudo setcap 'CAP_NET_ADMIN=ep' /usr/bin/iw
    #os.system("/sbin/iw dev wlo1 scan flush")
    wlans = os.popen(f'/sbin/iw {iface} scan').read()
    return wlans

def parse_iw_scan(wlanstr, iface="wlo1", mtime=None):
    # FIXME: in a future version, use directly RTNETLINK since iw --help says "Do NOT screenscrape this tool, we don't consider its output stable"
    # Developed and tested on Debian Bookworm
    # FIXME: how to get channel quality (not just signal level) ?
    wlans = []
    HTMODE = 0
    fplan = wifi_channel_plan()
    for line in wlanstr.splitlines():
        line = line.replace("\t", "").rstrip()
        if line.startswith("BSS ") and (line.endswith(f'(on {iface})') or line.endswith("-- associated")):
            wlans.append({"MAC": line[4:21]}) #mac = line.replace("BSS ", "").replace(f'(on {iface})', '').replace(" -- associated","")
            v_int=int(line[4:21].replace(':',''), 16)
            wlans[-1]["ID"] = v_int
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
        if "HT primary channel" in net:
            HT0_channel = int(net["HT primary channel"])
            assert(net["channel_20"] == HT0_channel) #assert(fplan.loc[HT0_channel].Fc == net["freq_20"])
            del net["HT primary channel"]
            if "DS Parameter set" in net:
                assert(HT0_channel == net["DS Parameter set"])
                del net["DS Parameter set"]
            if net["HT STA channel width"] == "any":
                BW = 40
                freq = fplan.loc[HT0_channel].Fc + (10 if net["HT secondary channel offset"]=="above" else - 10)
                net["freq_40"] = int(freq)
                if freq>5000:
                    net["channel_40"] = fplan[fplan.Fc==freq].index[0]
            del net["HT secondary channel offset"], net["HT STA channel width"]
        if "VHT channel width" in net:
            BW_code = int(net["VHT channel width"][0])
            if BW_code==1: BW=80
            elif BW_code>1: BW=160
            VHT_channel = int(net["VHT center freq segment 1"])
            if VHT_channel>=32:
                freq = fplan.loc[VHT_channel].Fc
                assert(net["VHT center freq segment 2"] == '0' and not "VHT center freq segment 3" in net)
                assert(BW == fplan.loc[VHT_channel].BW)
                net["freq_VHT"] = int(freq)
                net["channel_VHT"] = fplan[fplan.Fc==freq].index[0]
            del net["VHT center freq segment 2"], net["VHT center freq segment 1"], net["VHT channel width"]
        net["chanbw"] = BW
        if "freq_VHT" in net:
            net["Fc"] = net["freq_VHT"]
            net["Channel"] = net["channel_VHT"]
        elif "freq_40" in net and "channel_40" in net:
            net["Fc"] = net["freq_40"]
            net["Channel"] = net["channel_40"]
        else:
            net["Fc"] = net["freq_20"]
            net["Channel"] = net["channel_20"]
    df = pd.DataFrame(wlans)
    df["fmin"] = df.Fc - df.chanbw//2
    df["fmax"] = df.Fc + df.chanbw//2
    df['Time'] = int(time()) if mtime is None else mtime
    df.set_index("ID", inplace=True)
    return df

from pathlib import Path
def get_ubnt_wlans(username):
    cmd = f'''ssh -oHostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedKeyTypes=ssh-rsa -i {Path.home()}/.ssh/id_rsa {username}@192.168.1.20 "iwlist ath0 scan"''' # -legacy ?
    wlans = os.popen(cmd).read()
    return wlans

def parse_iwlist_scan(wlans, mtime=None):
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
    if len(wlan_nets)==0:
        return None
    df = pd.DataFrame(wlan_nets)
    df.set_index("ID", inplace=True)
    df['Time'] = int(time()) if mtime is None else mtime
    if not "Channel" in df.columns:
        df["Channel"] = None
    df["fmin"] = df.center1 - df.chanbw//2
    df["fmax"] = df.center1 + df.chanbw//2
    df.rename(columns={"center1": "Fc", "ESSID": "SSID", "Frequency": "freq_20", "Channel": "channel_20"}, inplace=True)
    #df2 = df[['MAC', 'Fc', 'chanbw', 'fmin', 'fmax', 'Time', 'Signal', 'SSID', 'Country', 'Environment', 'Channels', 'channel_20', 'freq_20', 'freq_40', 'channel_40', 'freq_VHT', 'channel_VHT']].copy()
    #df.Channel = df.Channel.astype(pd.Int16Dtype())
    return df # wlans,wlan_nets,

def filter_mto(nets):
    MTO_MIN = 5600
    MTO_MAX = 5650
    # not (fmax< mto_min or fmin>mto_max) => fmax>= mto_min and fmin<=mto_max
    idx = np.logical_and(nets.fmin<MTO_MAX, nets.fmax>MTO_MIN)
    return nets[idx]

def store(df, dbfilename="wlans.db"):
    df_static_fields = ["MAC", "ESSID", "fmin","fmax","Frequency", "ieee_mode", "center1", "chanbw", "Channel"]
    df_dyn_fields = ["Signal", "Quality", "Noise", "Time"]
    df1 = df[df_static_fields]
    df2 = df[df_dyn_fields]
    conn = sqlite3.connect(dbfilename)
    try:
        df1_existing = pd.read_sql("select * from networks", conn)
        df1_existing.set_index('Address', inplace=True)
        #df1_final = pd.merge(df1_existing, df1, how='outer"')
        df1_final = pd.concat([df1_existing, df1]).drop_duplicates()
    except:
        df1_final = df1
        print("create table")
    df1_final.to_sql('networks', conn, if_exists='replace', dtype={'Address': 'INTEGER PRIMARY KEY AUTOINCREMENT'}) # replace is for whole table, not per record
    df2.to_sql('measurements', conn, if_exists='append')
    df1_final.to_excel(dbfilename + ".xlsx")
    return df1, df2

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--iw", "-i", help="Internal scan", action='store_true', default=False)
    parser.add_argument("--ssh", "-s", help="SSH scan through router", action='store_true', default=False)
    parser.add_argument("--adb", "-a", help="ADB scan through smartphone", action='store_true', default=False)
    parser.add_argument("--gps", "-g", help="GPS log through smartphone", action='store_true', default=False)
    parser.add_argument("--db", "-d", help="Log in SQLite DB", default=None)
    parser.add_argument("--print", "-p", help="Print on console", action='store_true', default=True)
    args = parser.parse_args()

    while (True):
        if args.gps:
            lon,lat = adb_gps()
        if args.iw:
            df_iw = parse_iw_scan(iw_scan())
            if args.gps: df_iw=geodf(df_iw, lon, lat)
        if args.ssh:
            df_ssh = parse_iwlist_scan(get_ubnt_wlans())
            if args.gps: df_ssh=geodf(df_ssh, lon, lat)
        if args.adb:
            df_adb = adb_scan()
            if args.gps: df_adb=geodf(df_adb, lon, lat)
        if args.print:
            #print('\033[2J')
            print('_______________________________\n')
            if args.iw: print(df_iw)
            if args.ssh: print(df_ssh)
            if args.adb: print(df_adb)
        sleep(10)
