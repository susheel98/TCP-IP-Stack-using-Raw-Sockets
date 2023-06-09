#!/usr/bin/env python3
import socket, sys, time, argparse, random
from struct import *
from urllib.parse import urlparse

# The function accepts a integer number and checks if the port number is occupied or not using the SOCK_STREAM
# FYI: The socket.SOCK_STREAM is used only for port availability check. The main logic implements raw send and receive sockets in the main().
def is_port_in_use(port: int) -> bool:
	s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	s.settimeout(1)
	try:
		s.connect_ex(("localhost", port))
		return True
	except timeout:
		return True
	except:
		return False

# Checksum function needed for the calculation of TCP checksum
# The function accepts a byte array as an input and computes the network order sum and performs a ones complement on the sum.
def sendChecksum(msg):
	s = 0
	if(len(msg)%2!=0):
		msg+=b'\x00'
	for i in range(0, len(msg), 2):
		w = msg[i] + (msg[i+1] << 8 )
		s = s + w
	s = (s>>16) + (s & 0xffff)
	s = s + (s >> 16)
	#complement and mask to 4 byte short
	s = ~s & 0xffff
	return s

# Performs the same function as the sendChecksum() but the resulting byte order is reversed.
def receiveChecksum(source_string):
    sum = 0
    countTo = (len(source_string)/2)*2
    count = 0
    while count<countTo-1:
        thisVal = source_string[count + 1]*256 + source_string[count]
        sum = sum + thisVal
        sum = sum & 0xffffffff # Necessary?
        count = count + 2

    if countTo<len(source_string):
        sum = sum + source_string[len(source_string) - 1]
        sum = sum & 0xffffffff # Necessary?

    sum = (sum >> 16)  +  (sum & 0xffff)
    sum = sum + (sum >> 16)
    answer = ~sum
    answer = answer & 0xffff

    # Swap bytes. Bugger me if I know why.
    answer = answer >> 8 | (answer << 8 & 0xff00)

    return answer

# sendTCPPacket() function takes the socket dedicated to send, seq_no, ack_no, data to send and type of message(SYN,ACK,FIN..) as its parameters.
# The function builds the appropriate IP and TCP Header(computes checksum) and sends over the packet.
def sendTCPPacket(sendSocket, seq_no, ack_no, data, flag_type):

	global source_ip, dest_ip, port_no
	packet = ''

	# IP Header fields
	ip_ihl = 5
	ip_ver = 4
	ip_tos = 0
	ip_tot_len = 0
	ip_id = 0
	ip_frag_off = 0
	ip_ttl = 255
	ip_proto = socket.IPPROTO_TCP
	ip_check = 0
	ip_saddr = socket.inet_aton ( source_ip )
	ip_daddr = socket.inet_aton ( dest_ip )

	ip_ihl_ver = (ip_ver << 4) + ip_ihl

	# the ! in the pack format string means network order
	ip_header = pack('!BBHHHBBH4s4s' , ip_ihl_ver, ip_tos, ip_tot_len, ip_id, ip_frag_off, ip_ttl, ip_proto, ip_check, ip_saddr, ip_daddr)

	# TCP Header fields
	tcp_source = port_no	# source port
	tcp_dest = 80	# destination port
	tcp_seq = seq_no
	tcp_ack_seq = ack_no
	tcp_doff = 5	#4 bit field, size of tcp header, 5 * 4 = 20 bytes
	#TCP Flags
	tcp_fin = 0
	tcp_syn = 0
	tcp_rst = 0
	tcp_psh = 0
	tcp_ack = 0
	tcp_urg = 0
	
	if(flag_type=="SYN"):
		tcp_syn = 1
	if(flag_type=="ACK"):
		tcp_ack = 1
	if(flag_type=="FIN"):
		tcp_fin = 1
		tcp_ack = 1
	tcp_window = socket.htons (5840)	#	maximum allowed window size
	tcp_check = 0
	tcp_urg_ptr = 0

	tcp_offset_res = (tcp_doff << 4) + 0
	tcp_flags = tcp_fin + (tcp_syn << 1) + (tcp_rst << 2) + (tcp_psh <<3) + (tcp_ack << 4) + (tcp_urg << 5)

	# The ! in the pack format string means network order
	tcp_header = pack('!HHLLBBHHH' , tcp_source, tcp_dest, tcp_seq, tcp_ack_seq, tcp_offset_res, tcp_flags,  tcp_window, tcp_check, tcp_urg_ptr)

	user_data = data
	
	rule = "!"+str(len(user_data))+'s'
	test = pack(rule,user_data.encode('utf-8'))

	# pseudo header fields
	source_address = socket.inet_aton( source_ip )
	dest_address = socket.inet_aton(dest_ip)
	placeholder = 0
	protocol = socket.IPPROTO_TCP
	tcp_length = len(tcp_header) + len(user_data)

	psh = pack('!4s4sBBH' , source_address , dest_address , placeholder , protocol , tcp_length);
	psh = psh + tcp_header + user_data.encode('utf-8');

	tcp_check = sendChecksum(psh)
	#print tcp_checksum

	# make the tcp header again and fill the correct checksum - remember checksum is NOT in network byte order
	tcp_header = pack('!HHLLBBH' , tcp_source, tcp_dest, tcp_seq, tcp_ack_seq, tcp_offset_res, tcp_flags,  tcp_window) + pack('H' , tcp_check) + pack('!H' , tcp_urg_ptr)

	# final full packet - syn packets dont have any data
	packet = ip_header + tcp_header + user_data.encode('utf-8')

	sendSocket.sendto(packet, (dest_ip , 0 ))

# The receiveCorrectTCPacket takes the socket dedicated to receive packets as an argument and returns the TCP data of the packet
# next expected in the TCP Connection queue.
def receiveCorrectTCPPacket(recvSocket, sendSocket, data, flag_type, hasFileTransferBegun = False):
	# TODO: Logic of the function
	global seq_no, prev_seq, prev_ack
	recvParameters = None
	while(recvParameters == None):
		recvParameters = receiveTCPPacket(recvSocket)

		if(recvParameters != None):
			if(not hasFileTransferBegun):
				# Check if the received Packet acknowledges the previously sent TCP Packet.
				# If not resend the Packet and continue listening for more packets.
				if(recvParameters[1] != seq_no):
					recvParameters = None
					sendTCPPacket(sendSocket, prev_seq, prev_ack, data,flag_type)
			else:
				if(recvParameters[0] != ack_no):
					recvParameters = None
					sendTCPPacket(sendSocket, seq_no, ack_no, data,flag_type)

	return recvParameters

# This function expects the Source IP address and the destination port from the extracted receive packet as arguments.
# Checks if this packet is destined for this program.
def CheckIfCorrectPacket(source_ip, dest_port, ttl):
	global dest_ip, port_no
	if(source_ip != dest_ip or dest_port != port_no or ttl < 1):
		return False
	return True

def isCheckSumCorrect(tcp_header, data):
	global source_ip, dest_ip

	# Unpack the TCP Header
	tcph = unpack('!HHLLBBHHH' , tcp_header)
	# Extract the checksum from the unpacked TCP Header before replcing it with 0
	tcp_checksum = tcph[7]
	offset = tcph[4] >> 4
	reserved = tcph[4] & 0xF
	tcp_header_repack = pack('!HHLLBBHHH' , tcph[0], tcph[1], tcph[2], tcph[3], tcph[4], tcph[5],  tcph[6], 0, tcph[8])

	# pseudo header fields
	source_address = socket.inet_aton(dest_ip)
	dest_address = socket.inet_aton(source_ip)
	placeholder = 0
	protocol = socket.IPPROTO_TCP
	tcp_length = len(tcp_header_repack) + len(data)

	psh = pack('!4s4sBBH' , source_address , dest_address , placeholder , protocol , tcp_length);
	psh = psh + tcp_header_repack + data

	# Calculate the checksum of the repacked Header
	tcp_check = receiveChecksum(psh)

	# Check if the extracted checksum is equal to the calculated checksum
	if(tcp_check == tcp_checksum):
		return True
	else:
		print("Checksum Incorrect!")
		return False

# The receivePacket() functions takes the receive raw socket as an input and fetches one packet from the kernel receive packet buffer.
def receiveTCPPacket(recvSocket):

	global tcp_payload_len, isTransferComplete, pushEncountered
	packet = recvSocket.recvfrom(65565)

	#packet string from tuple
	packet = packet[0]	
	#take first 20 characters for the ip header
	ip_header = packet[0:20]
	
	#now unpack them :)
	iph = unpack('!BBHHHBBH4s4s' , ip_header)
	
	version_ihl = iph[0]
	version = version_ihl >> 4
	ihl = version_ihl & 0xF
	
	iph_length = ihl * 4
	
	ttl = iph[5]
	protocol = iph[6]
	s_addr = socket.inet_ntoa(iph[8])
	d_addr = socket.inet_ntoa(iph[9])
	
	tcp_header = packet[iph_length:iph_length+20]
	
	#now unpack them :)
	tcph = unpack('!HHLLBBHHH' , tcp_header)
	
	source_port = tcph[0]
	dest_port = tcph[1]
	sequence = tcph[2]
	acknowledgement = tcph[3]
	doff_reserved = tcph[4]
	flags_reserved = tcph[5]
	tcp_checksum = tcph[7]

	if(not CheckIfCorrectPacket(str(s_addr), dest_port, ttl)):
		return None

	flag_urg = (flags_reserved & 32) >> 5
	flag_ack = (flags_reserved & 16) >> 4
	flag_psh = (flags_reserved & 8) >> 3
	flag_rst = (flags_reserved & 4) >> 2
	flag_syn = (flags_reserved & 2) >> 1
	flag_fin = flags_reserved & 1
	if(flag_fin == 1):
		isTransferComplete = True
		pushEncountered = True
	if(flag_psh == 1):
		pushEncountered = True 
	tcph_length = doff_reserved >> 4
	
	h_size = iph_length + tcph_length * 4
	data_size = len(packet) - h_size
	
	#get data from the packet
	data = packet[h_size:]
	tcp_payload_len = data_size

	# Verify the TCP Checksum of the received Packet
	if(not pushEncountered):
		if(not isCheckSumCorrect(tcp_header, packet[iph_length+20:])):
			return None
	
	return [sequence, acknowledgement, data_size, data]

# threeWayHandshake() accepts the send and receive sockets as input parameters.
# The function sends a SYN packet, receives the SYN ACK packet and sends out the ACK packet with the HTTP GET request.
def threeWayHandshake(sendSocket, recvSocket):
	global seq_no, ack_no, prev_seq, prev_ack, HTTP_msg

	prev_seq = seq_no
	prev_ack = ack_no
	
	# Step 1: Send the SYM packet to the Server
	sendTCPPacket(sendSocket, seq_no, ack_no, '',"SYN")
	seq_no += 1

	# Step 2: Receive the SYN ACK packet from the server
	recvParameters = receiveCorrectTCPPacket(recvSocket, sendSocket, '', "SYN")
	ack_no = recvParameters[0] + 1

	# Step 3: Send the ACK packet and the HTTP GET Request
	sendTCPPacket(sendSocket, seq_no, ack_no, '', "ACK")
	# HTTP_msg = 'GET /classes/cs5700f22/2MB.log HTTP/1.1\r\nHost: david.choffnes.com\r\nConnection: Keep-Alive\r\n\r\n'
	sendTCPPacket(sendSocket, seq_no, ack_no, HTTP_msg, "ACK")

	prev_seq = seq_no
	prev_ack = ack_no
	seq_no += len(HTTP_msg)
	recvParameters = receiveCorrectTCPPacket(recvSocket, sendSocket, HTTP_msg, "ACK")
	ack_no = recvParameters[0]

# getResponseHeaders() function accepts a HTTP response as an input.
# This function is called when the initial response containing the HTTP response headers is received from the WebServer.
# The function looks at the HTTP response code. If the code is anything other than 200. The status code is printed and program ends.
# The function returns seperated HTTP file content from the response headers if the status code is 200.
def getResponseHeaders(http_msg):
	response_header = ''
	flag = 0
	pos=0
	initial_response = b''
	for i in range(len(http_msg)):
		letter = chr(http_msg[i])
		pos += 1

		if(letter != '\r' and letter !='\n'):
			response_header += letter

		# Check if terminating condition is reached.
		if(letter == '\r' or letter=='\n'):
			flag=flag+1
		else:
			flag=0
		
		if(flag==2):
			if('HTTP' in response_header):
				parsedData = response_header.split()
				responseCode = int(parsedData[1])
				if(responseCode != 200):
					print("The URL that you entered returned a HTTP Response with Status Code: "+ str(responseCode))
					exit(0)
			response_header=''
		elif(flag==4):
			break
	
	for i in range(pos, len(http_msg)):
		initial_response+=http_msg[i].to_bytes(1, 'big')
	
	return initial_response

# getFileContent() function accepts the send and receive raw sockets as input.
# This function is triggered after the TCP handshake. Function receives a HTTP message from the server checks if the message 
# is destined for this program and if the checksum is correct. If everything is right, then the HTTP response is added to the outfile buffer.
# Each of the server HTTP message is ACKed by this function.
def getFileContent(sendSocket, recvSocket):
	global seq_no, ack_no, prev_seq, prev_ack, isTransferComplete, domain, HTTP_msg, dest_file

	response = b''

	# Get the initial HTTP Response Packet and extract COntent-Length and partial file content from the packet.
	recvParameters = receiveCorrectTCPPacket(recvSocket, sendSocket, HTTP_msg, "ACK")
	initial_response = getResponseHeaders(recvParameters[3])
	response += initial_response
	ack_no = recvParameters[0] + recvParameters[2]
	sendTCPPacket(sendSocket, seq_no, ack_no, '', "ACK")
	binary_file = open(dest_file, "wb")
	binary_file.write(response)
	response = b''
	binary_file.close()
	
	# Get the remainder of the file data from the succeding TCP packets
	binary_file = open(dest_file, "ab")
	while(1):
		recvParameters = receiveCorrectTCPPacket(recvSocket, sendSocket, '', "ACK", True)
		ack_no = recvParameters[0] + recvParameters[2]
		if(recvParameters[2] != 0):
			binary_file.write(recvParameters[3])

		if(isTransferComplete):
			ack_no += 1
			prev_ack = ack_no
			sendTCPPacket(sendSocket, seq_no, ack_no, '', "FIN")
			break

		sendTCPPacket(sendSocket, seq_no, ack_no, '', "ACK")
	binary_file.close()


def main():
	global source_ip, dest_ip, seq_no, ack_no, port_no, isTransferComplete, HTTP_msg, dest_file, pushEncountered

	# Parse Command line arguments
	parser = argparse.ArgumentParser(description='Raw HTTP GET Socket')
	parser.add_argument('url', help='HTTP GET URL')
	args = parser.parse_args()
	
	# Set the Source IP Address of the machine running the script
	hostname = socket.gethostname()
	hostname += ".local"
	source_ip = socket.gethostbyname(hostname)

	# Set the Destination IP Address from the command line URL
	domain = urlparse(args.url).netloc
	path = urlparse(args.url).path

	# If the URL path is empty then the index.html file is fetched and downloaded locally.
	if(path == ''):
		path = '/'
		dest_file = 'index.html'
	# If the URL path ends with / then also the index.html file is fetched and downloaded locally.
	elif(path[-1] == '/'):
		dest_file = 'index.html'
	# If the path of the URL is a file then the file in question is fetched and downloaded locally.
	else:
		dest_file = path.split('/')[-1]
	
	# The IP address is fetched for the domain name extracted from the URL
	try:
		dest_ip = socket.gethostbyname(domain)
	except:
		print("Error encountered while resolving the IP address of the domain!")
		exit(0)

	# A random port number is chosen and checked if its open using the is_port_in_use(port) function.
	while(1):
		port = random.randint(1024, 65535)
		if(is_port_in_use(port)):
			port_no = port
			break
	
	# Setting the Sequence Number to a random number and Acknowledgement Number to 0
	seq_no = random.randint(0, 4294967295)
	ack_no = 0

	# Create Raw sockets to send and receive packets
	try:
		sendSocket = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW)
		recvSocket = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_TCP)
	except(socket.error , msg):
		print('Socket could not be created. Error Code : ' + str(msg[0]) + ' Message ' + msg[1])
		sys.exit()

	# Setting the HTTP GET message with the appropriate parameters
	HTTP_msg = 'GET '+path+' HTTP/1.1\r\nHost: '+domain+'\r\nConnection: Keep-Alive\r\n\r\n'
	isTransferComplete = False
	pushEncountered = False
	
	# Perform TCP Three way Handshake
	print("Performing TCP Handshake............")
	threeWayHandshake(sendSocket, recvSocket)
	print("TCP Handshake Complete!")
	print("Fetching the Contents of the request/file..................")
	
	# Fetched the response of the previously built HTTP GET request
	getFileContent(sendSocket, recvSocket)
	print("The Contents of the file downloaded to: "+dest_file)
main()
