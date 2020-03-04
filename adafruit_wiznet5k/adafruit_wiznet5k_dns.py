# The MIT License (MIT)
#
# (c) Copyright 2009-2010 MCQN Ltd
# Modified by Brent Rubell for Adafruit Industries, 2020
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
"""
`adafruit_wiznet5k_dns`
================================================================================

Pure-Python implementation of the Arduino DNS client for WIZnet 5k-based
ethernet modules.

* Author(s): MCQN Ltd, Brent Rubell

"""
import gc
import time
from random import getrandbits
from micropython import const
import adafruit_wiznet5k.adafruit_wiznet5k_socket as socket
from adafruit_wiznet5k.adafruit_wiznet5k_socket import htons

# pylint: disable=bad-whitespace

QUERY_FLAG             = const(0x00)
OPCODE_STANDARD_QUERY  = const(0x00)
RECURSION_DESIRED_FLAG = 1<<8

TYPE_A   = const(0x0001)
CLASS_IN = const(0x0001)
DATA_LEN = const(0x0004)

# Return codes for gethostbyname
SUCCESS          = const(1)
TIMED_OUT        = const(-1)
INVALID_SERVER   = const(-2)
TRUNCATED        = const(-3)
INVALID_RESPONSE = const(-4)

DNS_PORT         = const(0x35) # port used for DNS request
# pylint: enable=bad-whitespace

class DNS:
    """W5K DNS implementation.

    :param iface: Network interface
    """
    def __init__(self, iface, dns_address, debug=False):
        self._debug = debug
        self._iface = iface
        socket.set_interface(iface)
        self._sock = socket.socket(type=socket.SOCK_DGRAM)
        self._sock.settimeout(1)

        self._dns_server = dns_address
        self._host = 0
        self._request_id = 0 # request identifier
        self._pkt_buf = bytearray()

    def gethostbyname(self, hostname):
        """Translate a host name to IPv4 address format.
        :param str hostname: Desired host name to connect to.

        Returns the IPv4 address as a bytearray if successful, -1 otherwise.
        """
        if self._dns_server is None:
            return INVALID_SERVER
        self._host = hostname
        # build DNS request packet
        self._build_dns_header()
        self._build_dns_question()

        # Send DNS request packet
        self._sock.connect((self._dns_server, DNS_PORT))
        if self._debug:
            print("* DNS: Sending request packet...")
        self._sock.send(self._pkt_buf)

        # wait and retry 3 times for a response
        retries = 0
        addr = -1
        while (retries < 3) and (addr == -1):
            addr = self._parse_dns_response()
            if addr == -1 and self._debug:
                print("* DNS ERROR: Failed to resolve DNS response, retrying...")
            retries += 1

        self._sock.close()
        return addr

    def _parse_dns_response(self): # pylint: disable=too-many-return-statements, too-many-branches, too-many-statements, too-many-locals
        """Receives and parses DNS query response.
        Returns desired hostname address if obtained, -1 otherwise.

        """
        # wait for a response
        start_time = time.monotonic()
        packet_sz = self._sock.available()
        while packet_sz <= 0:
            packet_sz = self._sock.available()
            if (time.monotonic() - start_time) > 1.0:
                if self._debug:
                    print("* DNS ERROR: Did not receive DNS response!")
                return -1
            time.sleep(0.05)
        # recv packet into buf
        self._pkt_buf = self._sock.recv()

        if self._debug:
            print("DNS Packet Received: ", self._pkt_buf)

        # Validate request identifier
        xid = int.from_bytes(self._pkt_buf[0:2], 'l')
        if not xid == self._request_id:
            if self._debug:
                print("* DNS ERROR: Received request identifer {} \
                      does not match expected {}".format(xid, self._request_id))
            return -1
        # Validate flags
        flags = int.from_bytes(self._pkt_buf[2:4], 'l')
        if not flags == 0x8180:
            if self._debug:
                print("* DNS ERROR: Invalid flags, ", flags)
            return -1
        # Number of questions
        qr_count = int.from_bytes(self._pkt_buf[4:6], 'l')
        if not qr_count >= 1:
            if self._debug:
                print("* DNS ERROR: Question count >=1, ", qr_count)
            return -1
        # Number of answers
        an_count = int.from_bytes(self._pkt_buf[6:8], 'l')
        if self._debug:
            print("* DNS Answer Count: ", an_count)
        if not an_count >= 1:
            return -1

        # Parse query
        ptr = 12
        name_len = 1
        while name_len > 0:
            # read the length of the name
            name_len = self._pkt_buf[ptr]
            if name_len == 0x00:
                # we reached the end of this name
                ptr += 1 # inc. pointer by 0x00
                break
            # advance pointer
            ptr += name_len + 1

        # Validate Query is Type A
        q_type = int.from_bytes(self._pkt_buf[ptr:ptr+2], 'l')
        if not q_type == TYPE_A:
            if self._debug:
                print("* DNS ERROR: Incorrect Query Type: ", q_type)
            return -1
        ptr += 2

        # Validate Query is Type A
        q_class = int.from_bytes(self._pkt_buf[ptr:ptr+2], 'l')
        if not q_class == TYPE_A:
            if self._debug:
                print("* DNS ERROR: Incorrect Query Class: ", q_class)
            return -1
        ptr += 2

        # find answer response, TODO: Remove in favor of stream-like approach above
        idx = self._pkt_buf.find(b'\xc0\x0c')
        # Validate Type A
        ans_type = int.from_bytes(self._pkt_buf[idx+2:idx+4], 'l')
        if not ans_type == TYPE_A:
            if self._debug:
                print("* DNS ERROR: Incorrect Answer Type: ", ans_type)
            return -1
        # Validate Class IN
        class_type = int.from_bytes(self._pkt_buf[idx+4:idx+6], 'l')
        if not class_type == CLASS_IN:
            if self._debug:
                print("* DNS ERROR: Incorrect Class Type: ", class_type)
            return -1
        # Ignore 2-byte TTL
        # Validate addr is IPv4
        data_length = int.from_bytes(self._pkt_buf[idx+10:idx+12], 'l')
        if not data_length == DATA_LEN:
            if self._debug:
                print("* DNS ERROR: Unexpected Data Length: ", data_length)
            return -1
        # Return address
        gc.collect()
        return self._pkt_buf[idx+12:idx+16]

    def _build_dns_header(self):
        """Builds DNS header."""
        # generate a random, 16-bit, request identifier
        self._request_id = getrandbits(16)

        # ID, 16-bit identifier
        self._pkt_buf.append(self._request_id >> 8)
        self._pkt_buf.append(self._request_id & 0xFF)

        # Flags (0x0100)
        self._pkt_buf.append(0x01)
        self._pkt_buf.append(0x00)

        # QDCOUNT
        self._pkt_buf.append(0x00)
        self._pkt_buf.append(0x01)
        # ANCOUNT
        self._pkt_buf.append(0x00)
        self._pkt_buf.append(0x00)
        # NSCOUNT
        self._pkt_buf.append(0x00)
        self._pkt_buf.append(0x00)
        #ARCOUNT
        self._pkt_buf.append(0x00)
        self._pkt_buf.append(0x00)

    def _build_dns_question(self):
        """Build DNS question"""
        host = self._host.decode('utf-8')
        host = host.split(".")
        # write out each section of host
        for i, _ in enumerate(host):
            # append the sz of the section
            self._pkt_buf.append(len(host[i]))
            # append the section data
            self._pkt_buf += host[i]
        # end of the name
        self._pkt_buf.append(0x00)
        # Type A record
        self._pkt_buf.append(htons(TYPE_A) & 0xFF)
        self._pkt_buf.append(htons(TYPE_A) >> 8)
        # Class IN
        self._pkt_buf.append(htons(CLASS_IN) & 0xFF)
        self._pkt_buf.append(htons(CLASS_IN) >> 8)
