#!/usr/bin/env python3
# This file is part of the Civilsphere AI VPN
# See the file 'LICENSE' for copying permission.
# Author: Sebastian Garcia,
#         eldraco@gmail.com, sebastian.garcia@agents.fel.cvut.cz
# Author: Veronica Valeros
#         vero.valeros@gmail.com, veronica.valeros@aic.fel.cvut.cz

import os
import sys
import time
import logging
import configparser
import subprocess
import signal
from common.database import redis_connect_to_db
from common.database import redis_create_subscriber
from common.database import redis_subscribe_to_channel
from common.database import add_pid_profile_name_relationship
from common.database import add_profile_name_pid_relationship
from common.database import add_profile_ip_relationship
from common.database import get_vpn_client_ip_address


def revoke_profile(loc_profile):
    """
    Revoke a given profile using the 'del-peer' command.

    loc_profile (str): The local profile identifier for the profile to revoke.
    returns (bool): True if the profile was successfully revoked, else False.
    """
    action_status = False
    try:
        # Call the del-peer function using subprocess
        delpeer_result = subprocess.run(
                ['/app/del-peer', loc_profile],
                capture_output=True,
                text=True,
                check=True)
        # Return true only if the return code is 0
        if delpeer_result.returncode == 0:
            action_status = True
    except subprocess.CalledProcessError as loc_err:
        logging.error("del-peer failed, return code %s: %s",
                      loc_err.returncode,
                      loc_err.output)
    except OSError as loc_err:
        logging.error("del-peer failed with OSError: %s",
                      loc_err)
    except ValueError as loc_err:
        logging.error("del-peer failed, invalid arguments for subprocess: %s",
                      loc_err)

    # Return action_status for any of the cases
    return action_status


def generate_profile(loc_profile, loc_path, loc_client_ip):

    """
    This function generates a new profile for a client_name.

    loc_profile: profile name for the client
    loc_path: path were to store the profile
    loc_client_ip: IP assigned to the client
    """
    action_status = False
    try:
        # This is where we call the add-peer using subprocess
        addpeer_result = subprocess.run(
                ['/app/add-peer', loc_profile, loc_path, loc_client_ip],
                capture_output=True,
                text=True,
                check=True)
        if addpeer_result.returncode == 0:
            action_status = True
    except subprocess.CalledProcessError as loc_err:
        logging.error("add-peer failed, return code %s: %s",
                      loc_err.returncode,
                      loc_err.output)
    except OSError as loc_err:
        logging.error("add-peer failed with OSError: %s",
                      loc_err)
    except ValueError as loc_err:
        logging.error("add-peer failed, invalid arguments for subprocess: %s",
                      loc_err)

    # Return action_status for any of the cases
    return action_status


def start_traffic_capture(loc_profile, loc_client_ip, loc_path):
    """
    This function starts a tcpdump process to capture the traffic and store the
    pcap for a given client and IP.

    loc_profile: profile name for the client
    loc_client_ip: IP assigned to the client
    loc_path: path assigned to the client
    :return: The PID of the started tcpdump process or False on failure.

    """
    loc_pid = None

    # Identify which tcpdump to run
    try:
        cmd_tcpdump = os.popen('which tcpdump').read().strip()
    except OSError as loc_err:
        logging.error('Failed to find tcpdump: %s', loc_err)
        return False

    # Number used to differentiate pcaps if there's more than one
    pcap_number = str(time.monotonic()).split('.')[1]

    # Create the tcpdump file name
    profile_pcap_path = f'{loc_path}/{loc_profile}'
    profile_pcap_name = f'{loc_profile}_{loc_client_ip}_{pcap_number}.pcap'
    profile_file_path = f'{profile_pcap_path}/{profile_pcap_name}'

    # Prepare the arguments for the subprocess
    args = [cmd_tcpdump,
            "-qq",
            "-n",
            "-U",
            "-l",
            "-s0",
            "-i",
            "any",
            "host", loc_client_ip,
            "-w", profile_file_path]

    try:
        # Start the subprocess
        with subprocess.Popen(args,
                              start_new_session=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              stdin=subprocess.PIPE) as process:

            # Get the PID
            loc_pid = process.pid

    except ValueError:
        logging.error('Invalid arguments provided to subprocess.Popen')
    except OSError as loc_err:
        logging.error('OS error occurred: %s', loc_err)

    # Return the PID
    return loc_pid


def stop_traffic_capture(loc_client_pid):
    """
    Immediately stops the traffic capture process with the given PID.

    loc_client_pid (int): The PID of the traffic capture process to stop.
    :returns: True if the process was stopped successfully, False otherwise.
    """

    # Make sure we don't try to kill a non existent PID
    if loc_client_pid is None:
        logging.error("Invalid PID: None provided.")
        return False

    # Terminate the process immediately
    try:
        os.kill(loc_client_pid, signal.SIGKILL)
        # Wait for the process to be killed
        os.waitpid(loc_client_pid, 0)
        logging.info("Process with PID %d was killed.", loc_client_pid)
        return True
    except OSError as loc_err:
        logging.error('Failed to kill process with PID %d: %s',
                      loc_client_pid,
                      loc_err)
    except TypeError as loc_err:
        logging.error('Invalid PID type: %s', loc_err)
    return False


def set_profile_static_ip(CLIENT_NAME, CLIENT_IP):
    """
    This function creates sets an static IP for the client profile by creating
    a file in the ccd/ directory with the IP set for the client.
    """
    try:
        # Lets not need this
        pass
    except Exception as err:
        logging.info('Exception in set_profile_static_ip: %s', err)
        return False


def read_configuration():
    # Read configuration file
    config = configparser.ConfigParser()
    config.read('config/config.ini')

    REDIS_SERVER = config['REDIS']['REDIS_SERVER']
    CHANNEL = config['REDIS']['REDIS_WIREGUARD_CHECK']
    LOG_FILE = config['LOGS']['LOG_WIREGUARD']
    SERVER_PUBLIC_URL = config['WIREGUARD']['SERVER_PUBLIC_URL']
    PKI_ADDRESS = config['WIREGUARD']['PKI_ADDRESS']
    PATH = config['STORAGE']['PATH']

    return REDIS_SERVER, CHANNEL, LOG_FILE, SERVER_PUBLIC_URL, PKI_ADDRESS, PATH


if __name__ == '__main__':
    # Read configuration
    REDIS_SERVER, CHANNEL, LOG_FILE, SERVER_PUBLIC_URL, PKI_ADDRESS, PATH = read_configuration()

    logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG, format='%(asctime)s, MOD_WIREGUARD, %(message)s')

    # Connecting to the Redis database
    try:
        redis_client = redis_connect_to_db(REDIS_SERVER)
    except Exception as err:
        logging.info('Unable to connect to the Redis database (%s): %s',
                     REDIS_SERVER,
                     err)
        sys.exit(-1)

    # Creating a Redis subscriber
    try:
        db_subscriber = redis_create_subscriber(redis_client)
    except Exception as err:
        logging.info('Unable to create a Redis subscriber: %s', err)
        sys.exit(-1)

    # Subscribing to Redis channel
    try:
        redis_subscribe_to_channel(db_subscriber, CHANNEL)
        logging.info("Connection and channel subscription to redis successful.")
    except Exception as err:
        logging.info('Channel subscription failed: %s', err)
        sys.exit(-1)

    try:
        # Checking for messages
        logging.info("Listening for messages")
        for item in db_subscriber.listen():
            # Every new message is processed and acted upon
            if item['type'] == 'message':
                logging.info('New message received in channel %s: %s',
                             item['channel'],
                             item['data'])
                if item['data'] == 'report_status':
                    redis_client.publish('services_status', 'MOD_WIREGUARD:online')
                    logging.info('Status Online')
                elif 'new_profile' in item['data']:
                    account_error_message = ""
                    logging.info('Received a new request for an WireGuard profile')
                    redis_client.publish('services_status', 'MOD_WIREGUARD:processing a new WireGuard profile')

                    # Get a client ip for wireguard
                    CLIENT_IP = get_vpn_client_ip_address('wireguard', redis_client)

                    if not CLIENT_IP == False:
                        # Parse the name obtained in the request
                        CLIENT_NAME = item['data'].split(':')[1]
                        redis_client.publish('services_status', f'MOD_WIREGUARD: assigning IP ({CLIENT_IP}) to client ({CLIENT_NAME})')

                        # Generate the Wireguard profile for the client
                        logging.info('Generating WireGuard profile %s with IP %s', CLIENT_NAME, CLIENT_IP)
                        status = generate_profile(CLIENT_NAME, PATH, CLIENT_IP)
                        if status == True:
                            set_profile_static_ip(CLIENT_NAME, CLIENT_IP)
                            # Store client:ip relationship for the traffic capture
                            if add_profile_ip_relationship(CLIENT_NAME, CLIENT_IP, redis_client):
                                PID = start_traffic_capture(CLIENT_NAME, CLIENT_IP, PATH)
                                if not PID == False:
                                    logging.info('Tcpdump started successfully (PID:%s)', PID)
                                    result = add_pid_profile_name_relationship(PID, CLIENT_NAME, redis_client)
                                    result = add_profile_name_pid_relationship(CLIENT_NAME, PID, redis_client)
                                    redis_client.publish('services_status', 'MOD_WIREGUARD:profile_creation_successful')
                                    redis_client.publish('provision_wireguard', 'profile_creation_successful')
                                    logging.info('profile_creation_successful')
                                else:
                                    account_error_message = "MOD_WIREGUARD: profile_creation_failed:cannot start tcpdump"
                            else:
                                account_error_message = "MOD_WIREGUARD: profile_creation_failed:cannot add profile_ip relationship to redis"
                        else:
                            account_error_message = "MOD_WIREGUARD: profile_creation_failed:failed to create a new profile"
                    else:
                        account_error_message = "MOD_WIREGUARD: profile_creation_failed:no available IP addresses found"

                    # Notify once if there is an error message
                    if account_error_message:
                        logging.info(account_error_message)
                        redis_client.publish('services_status', account_error_message)
                        redis_client.publish('provision_wireguard', account_error_message)

                elif 'revoke_profile' in item['data']:
                    account_error_message = ""
                    # Parse CLIENT_NAME and PID from message
                    CLIENT_NAME = item['data'].split(':')[1]
                    CLIENT_PID = int(item['data'].split(':')[2])
                    logging.info('Revoking profile %s and stopping traffic capture (%s)', CLIENT_NAME, CLIENT_PID)

                    # Revoke VPN profile
                    if revoke_profile(CLIENT_NAME):
                        # Stop the traffic capture by PID
                        status = stop_traffic_capture(CLIENT_PID)
                        logging.info('Result of stopping the traffic capture was {%s}', status)
                        if status:
                            # Account revoked successfully
                            redis_client.publish('services_status', 'MOD_WIREGUARD: profile_revocation_successful')
                            redis_client.publish('deprovision_wireguard', 'profile_revocation_successful')
                            logging.info('profile_revocation_successful')
                        else:
                            account_error_message = 'Unable to stop the traffic capture.'
                    else:
                        account_error_message = 'Unable to revoke the VPN profile.'

                    # Notify once if there is an error message
                    if account_error_message:
                        logging.info(account_error_message)
                        redis_client.publish('services_status', account_error_message)
                        redis_client.publish('deprovision_wireguard', account_error_message)

        redis_client.publish('services_status', 'MOD_WIREGUARD:offline')
        logging.info("Terminating")
        db_subscriber.close()
        redis_client.close()
        sys.exit(-1)
    except Exception as err:
        logging.info('Terminating via exception in main: %s', err)
        db_subscriber.close()
        redis_client.close()
        sys.exit(-1)
