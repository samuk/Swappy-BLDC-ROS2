#!/usr/bin/env python

#https://stackoverflow.com/questions/34337514/updated-variable-into-multiprocessing-python
#https://stackoverflow.com/questions/44219288/should-i-bother-locking-the-queue-when-i-put-to-or-get-from-it/44219646
import rospy
import os
import datetime
import subprocess
import json
import sbp.msg
import sys
import time
import operator

import threading, queue
from multiprocessing import Process, Value
from multiprocessing.managers import BaseManager

from sbp.client.loggers.udp_logger import UdpLogger
from sbp.client.drivers.pyserial_driver import PySerialDriver
from sbp.client import Handler, Framer
from sbp.client.loggers.json_logger import JSONLogger
from sbp.observation import SBP_MSG_OBS, MsgObs, SBP_MSG_GLO_BIASES, MsgGloBiases, SBP_MSG_BASE_POS_ECEF, MsgBasePosECEF
from sbp.observation import SBP_MSG_EPHEMERIS_BDS, MsgEphemerisBds, SBP_MSG_EPHEMERIS_GAL, MsgEphemerisGal, SBP_MSG_EPHEMERIS_GLO, MsgEphemerisGlo, SBP_MSG_EPHEMERIS_QZSS, MsgEphemerisQzss
#SBP_MSG_EPHEMERIS_GPS, MsgEphemerisGps
import argparse

class MsgObsWithPayload:
    def __init__(self, msg_obs, payload, source):
        self.msg_obs = msg_obs
        self.payload = payload
        self.source = source

# NTRIP host
NTRIP_HOST = rospy.get_param('/sbp_arbitrator/ntrip_host', "rtk2go.com")
NTRIP_PORT = rospy.get_param('/sbp_arbitrator/ntrip_port', 2101)
NTRIP_MOUNT_POINT = rospy.get_param('/sbp_arbitrator/ntrip_mount_point', "ER_Valldoreix_1")
#RADIO
RADIO_PORT =  rospy.get_param('/sbp_arbitrator/radio_port', "/dev/freewaveGXMT14")
RADIO_BAUDRATE = rospy.get_param('/sbp_arbitrator/radio_baudrate', 115200)
# UDP LOGGER
UDP_ADDRESS = rospy.get_param('/sbp_arbitrator/udp_address', "192.168.8.222")
UDP_PORT =  rospy.get_param('/sbp_arbitrator/udp_port', 55558)

# create instance of UdpLogger object
udp = UdpLogger(UDP_ADDRESS, UDP_PORT)

# shared integer for parallel processes that stores last ntrip/radio TOW
#q = Value('i', 0)

# get current year:month:day:hour
#def get_current_time():
#    now = datetime.datetime.now(datetime.timezone.utc)
#    return "{}:{}:{}:{}".format(now.year, now.month, now.day, now.hour)

def ntrip_corrections(q_ntrip):
    count = 0
    last_ntrip_epoch = None
    ntrip_epoch = None
    ntrip_msgs_to_send = [] # ntrip messages to be sent
    msg_array = []

    # run command to listen to ntrip client, convert from rtcm3 to sbp and from sbp to json redirecting the stdout
    str2str_cmd = ["str2str", "-in", "ntrip://{}:{}/{}".format(NTRIP_HOST, NTRIP_PORT, NTRIP_MOUNT_POINT)]
    rtcm3tosbp_cmd = ["rtcm3tosbp"]#, "-d", get_current_time()]
    cmd = "{} 2>/dev/null| {} | sbp2json".format(' '.join(str2str_cmd), ' '.join(rtcm3tosbp_cmd))
    p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    while True:
        line = p.stdout.readline().strip()
        try:
            json_msg = json.loads(line)
            # handle encoded JSON
            if 'data' in json_msg and 'payload' in json_msg['data']:
                json_msg = json_msg['data']
        except ValueError:
            continue

        # sanity check
        if 'msg_type' not in json_msg:
            continue

        # parse sbp msgs
        sbp_general_msg = sbp.msg.SBP.from_json_dict(json_msg)

        # Mandatory to send msg 72, optional to send msg 117, 137, 138, 139, 141, 142
        if sbp_general_msg.msg_type == (72, 117, 137, 138, 139, 141, 142):
            sbp_general_msg.sender = 0
            udp.call(sbp_general_msg)

        # Arbitrate msg 74
        elif sbp_general_msg.msg_type == 74:
            #sbp_msg = MsgObs(sbp_general_msg)
            global ntrip_sender # get ntrip sender id
            if ntrip_sender is None:
                ntrip_sender = sbp_general_msg.sender
            q_ntrip.put(sbp_general_msg)
            print("Putting ntrip msg on queue")

def radio_corrections(q_radio):
    last_radio_epoch = None
    radio_epoch = None
    # messages to be sent next time
    radio_msgs_to_send = []
    #rospy.sleep(1.5)
    i = 0
    while True:
        q_radio.put(i)
        #print(d)
        #print(len(d))
        #print(str(d.msg_type) + " " + str(d.header.n_obs) + " " + str(d.header.t.tow))
        i += 1
        rospy.sleep(0.5)
    # with PySerialDriver(RADIO_PORT, baud=RADIO_BAUDRATE) as driver:
    #     print(driver.read)
    #     with Handler(Framer(driver.read, None, verbose=False)) as source:
    #         try:
    #             for msg, metadata in source.filter([SBP_MSG_OBS, SBP_MSG_GLO_BIASES, SBP_MSG_BASE_POS_ECEF]):
    #                 # change radio sender ID to ntrip to avoid temporal glitch
    #                 msg.sender = 65202
    #                 # update epoch
    #                 if msg.msg_type == 74:
    #                     radio_epoch = radio_tow = msg.header.t.tow
    #                 # break msgs into epochs
    #                 if last_radio_epoch is not None and radio_epoch > last_radio_epoch:
    #                     q.acquire()
    #                     last_tow = q.value
    #                     q.release()
    #                     # send radio msg only if its tow is greater than the latest registered tow
    #                     if radio_tow > last_tow:
    #                         q.acquire()
    #                         q.value = radio_tow
    #                         q.release()
    #                         print("RADIO msg sent for epoch: ", radio_tow)
    #                         n_seq = 0
    #                         n_glo = 0
    #                         for x in radio_msgs_to_send:
    #                             udp.call(x) # send msg to the piksi through udp
    #                             if x.msg_type == 74:
    #                                 n_seq += 1
    #                                 seq = x.header.n_obs
    #                                 tow2print = radio_tow
    #                             if x.msg_type == 117:
    #                                 n_glo +=1
    #                             else:
    #                                 seq = None
    #                                 tow2print = None
    #                             print("    Radio", x.msg_type, seq, tow2print)
    #                         print("===============================")
    #                         rospy.loginfo("Radio, %i, %i, %i", radio_tow, n_seq, n_glo)
    #                     #elif radio_tow <= last_tow:
    #                         #print("Ignoring radio msg with old tow")
    #                         #print("===============================")
    #                     radio_msgs_to_send = []
    #                     radio_msgs_to_send.append(msg)
    #                 else:
    #                     if radio_epoch is not None:
    #                         radio_msgs_to_send.append(msg)
    #                 last_radio_epoch = radio_epoch
    #         except KeyboardInterrupt:
    #             pass

# def arbitrate(msg_array, expected_packet, prev_tow, hist_length, wait_epoch):
#     # get info from queue and put into msg_array
#     msg_array = q.get()
#     for msg in msg_array:
#         packet = hex(msg.header.n_obs)
#         packet_seq = packet[2] # get total number of packets in a sequence
#         packet_index = packet[3] # get the index of the packet (starting at 0)
#         if msg.t.tow == prev_tow:
#             if packet_index == expected_packet:
#                 udp.call(msg)
#                 msg_array.remove(msg)
#                 if packet_index == packet_seq:
#                     expected_packet = 0 #sequence is complete
#                 else:
#                     expected_packet += 1
#             else:
#                 if wait_epoch == hist_length:
#                     expected_packet = 0 # did not receive complete sequence, continue with next epoch after waiting hist_length
#                     wait_epoch = 0
#                 else:
#                     wait_epoch += 1
#
#         else if expected_packet == 0 and msg.t.tow >= prev_tow
#             if packet_index == 0 #new epoch
#                 udp.call(msg)
#                 msg_array.remove(msg) # remove message from list
#                 expected_packet += 1
#                 prev_tow = msg.header.t.tow
#     q.put(msg_array) # updaet queue with processed array

def parse_msg(msg):
    if msg.msg_type == 72:
        sbp_msg = MsgBasePosECEF(msg)
    elif msg.msg_type == 74:
        sbp_msg = MsgObs(msg)
    elif msg.msg_type == 117:
        sbp_msg = MsgGloBiases(msg)
    elif msg.msg_type == 137:
        sbp_msg = MsgEphemerisBds(msg)
    #if msg.msg_type == 138: ## cannot compile !!!!!!!!!!!!!!!!!!!!!!!
    #    sbp_msg = MsgEphemerisGps(sbp_general_msg)
    elif msg.msg_type == 139:
        sbp_msg = MsgEphemerisGlo(msg)
    elif msg.msg_type == 141:
        sbp_msg = MsgEphemerisGal(msg)
    elif msg.msg_type == 142:
        sbp_msg = MsgEphemerisQzss(msg)
    return sbp_msg

def get_queue_msgs(queue):
    msg_list = []
    while not queue.empty():
         msg_list.append(queue.get())
    return msg_list

def get_packet_index(msg):
    # returns packet seq and packet index
    packet = hex(msg.msg_obs.header.n_obs)
    return int(packet[2]), int(packet[3])

def check_existing_msgs(msg_list, new_msg):
    if not msg_list:
        msg_list.append(new_msg)
    else:
        for msg in msg_list:
            [_, msg_packet_index] = get_packet_index(msg)
            [_, new_msg_packet_index] = get_packet_index(new_msg)
            if msg.msg_obs.header.t.tow == new_msg.msg_obs.header.t.tow and new_msg_packet_index == msg_packet_index:
                #sanity check
                if msg.payload == new_msg.payload:
                    break
                else:
                    rospy.logwarn("Received 2 equivalent messages with the same PAYLOAD")
            else:
                #add msg to list
                msg_list.append(new_msg)

    return msg_list

if __name__ == '__main__':
    rospy.init_node('sbp_arbitrator', anonymous=True)
    ntrip_sender = None
    q_ntrip = queue.Queue()
    q_radio = queue.Queue()
    th1 = threading.Thread(target=ntrip_corrections,args=(q_ntrip,))
    th2 = threading.Thread(target=radio_corrections,args=(q_radio,))
    th1.start()
    th2.start()

    msgs_to_evaluate = []
    prev_tow = 0
    expected_packet = 0
    epoch_timeout = 5 # number of seconds to wait until timout

    # Arbitrate
    while True:
        ntrip_msgs = get_queue_msgs(q_ntrip)

        #print(ntrip_msgs.payload)
        # Evaluate tow and check if msg is repeated
        for msg in ntrip_msgs:
            msg_obs_with_payload = MsgObsWithPayload(parse_msg(msg), msg.payload, "Ntrip")
            if msg_obs_with_payload.msg_obs.header.t.tow >= prev_tow:
                msgs_to_evaluate = check_existing_msgs(msgs_to_evaluate, msg_obs_with_payload)
        # Order messages
        msgs_to_evaluate.sort(key=operator.attrgetter('msg_obs.header.t.tow', 'msg_obs.header.n_obs'))

        # Evaluate msgs to send
        for msg in msgs_to_evaluate:
            packet_seq, packet_index = get_packet_index(msg) # get the index of the packet (starting at 0)
            #print("Expected packet "+str(expected_packet))
            #print("Packet index"+str(packet_index))
            #print("tow"+str(msg.msg_obs.header.t.tow))
            #print("Previous tow"+str(prev_tow))
            if msg.msg_obs.header.t.tow == prev_tow:
                if packet_index == expected_packet:
                    udp.call(msg.msg_obs)
                    rospy.loginfo(str(msg.source) + ", " + str(msg.msg_obs.header.t.tow) + ", " + str(packet_index) + ", " + str(packet_seq))
                    msgs_to_evaluate.remove(msg)
                    if packet_index == packet_seq-1:
                        expected_packet = 0 #sequence is complete
                        print("Sequence is complete")
                    else:
                        expected_packet += 1
                else:
                    if abs((prev_tow - msg.msg_obs.header.t.tow)/1000) >= epoch_timeout:
                        print("Timing out")
                        expected_packet = 0 # did not receive complete sequence, continue with next epoch after waiting hist_length
            elif expected_packet == 0 and msg.msg_obs.header.t.tow >= prev_tow:
                if packet_index == 0: #new epoch
                    print("In here")
                    udp.call(msg.msg_obs)
                    rospy.loginfo(str(msg.source) + ", " + str(msg.msg_obs.header.t.tow) + ", " + str(packet_index) + ", " + str(packet_seq))
                    msgs_to_evaluate.remove(msg) # remove message from list
                    expected_packet += 1
                    prev_tow = msg.msg_obs.header.t.tow

        #msg.header.t.tow
        # evaluate if it is repeated
        #msgs_to_evaluate = append(d)

        #e = q_radio.get()
        #check sender iid if is radio or ntrip_port

        #print(ntrip_sender)
        #print(str(d.msg_type) + " " + str(d.header.n_obs) + " " + str(d.header.t.tow))
        #print(e)
