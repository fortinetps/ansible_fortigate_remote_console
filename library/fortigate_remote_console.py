#!/usr/bin/python
#
# Ansible module to manage fortigate devices through remote console access
# (c) 2019, Don Yao <@fortinetps>
# GNU General Public License v3.0+ (see COPYING or
# https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = '''
---
module: fortigate_remote_console
short_description: FortiGate Remote Console Module
version_added: "2.7"
description:
    - "This is fortigate_remote_console module, for FortiGate console access through remote console server (Cisco, Avocent, Raritan, MRV, ...)"
author:
    - Don Yao (@fortinetps)

notes:
    - Tested against FortiGate-501E v5.6.5 with Avocent ACS8000 and MRV LX4032
    - Only works with physical FortiGate appliance with serial console port
    - This module is good for FortiGate without network conneciton, but with remote console connection (OOB)
    - Or some action may cause FortiGate lose it is network connectivity, but OOB remote console connection stays
    - Use this module to factoryreset FortiGate
    - Use this module to bootstrap brand new FortiGate
    - Use this module to perform low level erase-disk

options:
    config:
        description:
            - Configuration to be backup
        required: true
        type: dict
        suboptions:
            filename:
                description:
                    - Configuration filename
                required: true
'''
EXAMPLES = '''
---
name: backup config
tags:
- hostname
fortios_api_system_config_restore:
  conn_params:
    fortigate_username: admin
    fortigate_password: test
    fortigate_ip: 1.2.3.4
    verify: false
  config:
  - filename: /firmware/backup_config.conf

'''

RETURN = '''
result:
    description: k/v pairs of firmware upgrade result
    returned: always
    type: dict
'''

import re
import sys
import time
import random
import pexpect
import datetime

from ansible.module_utils.basic import AnsibleModule

class fortigate_remote_console():
    def __init__(self, rcs_ip, rcs_username, rcs_password, rcs_fgt_username='admin', rcs_fgt_password='', rcs_fgt_port=None, rcs_fgt_cli=None):
        self.rcs_ip = rcs_ip
        self.rcs_username = rcs_username
        self.rcs_password = rcs_password
        self.rcs_fgt_port = rcs_fgt_port
        self.rcs_fgt_username = rcs_fgt_username
        self.rcs_fgt_password = rcs_fgt_password
        self.rcs_fgt_cli = rcs_fgt_cli

        self.rcs_prompt = None                          # CLI prompt for remote console server (rcs) itself
        self.rcs_console = None                         # Remote Console connection (for console access)
        self.rcs_fgt_prompt = None     # CLI prompt for device (FGT) connected to the remote console port

    ############################################################################
    def fortigate_remote_console_cli(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            # outputs.append(output)

            # for each command
            for command in self.rcs_fgt_cli[0].splitlines():
                self.rcs_console.sendline(command)
                time.sleep(1)
                index = self.rcs_console.expect(self.rcs_fgt_prompt)
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

                if index == 3:    # with this, it seems like hostname was changed in the middle of the command (mostly by set hostname)
                    hostname = self.rcs_console.before.splitlines()[-1].split(' ')[0]
                    # the first split find the last line, which contains the hostname
                    # the second split, in case FortiGate is inside configuration section or in global/vdom, FortiGate doesn't allow space in hostname
                    # update the hostname
                    self.rcs_fgt_prompt = ['dummy_placeholder', hostname + ' # ', hostname + ' \(.+\) # ', ' # ', ' login: ', 'to accept']

                elif index == 4 or index == 5:    # with this, it seems like password was changed in the middle of the command (mostly by set password)
                    # simple close the connection and return
                    outputs.append('It seems like password was changed in the middle of the console cli command execution')
                    self.rcs_console.close()
                    self.rcs_console = None
                    break

            rcs_result['status'] = 0
            rcs_result['changed'] = True

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.fortigate_remote_console_logout()
                self.rcs_console = None
            rcs_result['console_action_result'] = outputs
            return rcs_result

    ############################################################################
    def fortigate_remote_console_reboot(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            # outputs.append(output)

            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)

            # send exec factoryreset command
            self.rcs_console.sendline('exec reboot')
            self.rcs_console.expect(['Do you want to continue\? \(y\/n\)'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            # send 'y' to confirm
            self.rcs_console.send('y')

            # factoryreset reboots device, and it could reboot more than once
            index = 0
            while index != 1 and index != 2:
                index = self.rcs_console.expect(['dummy_placeholder', 'to accept', ' login: ', 'System is starting', 'please wait for reboot'], timeout=1800)  
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

                if index == 3:
                    wait_for_reboot = False # reset wait_for_reboot flag
                if index == 4:
                    wait_for_reboot = True  # we received "please wait for reboot" message
                if index == 1 or index == 2:
                    if wait_for_reboot: # skip this login prompt
                        index = 0   # reset the index
                        continue

            rcs_result['status'] = 0
            rcs_result['changed'] = True

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.rcs_console.close()
                self.rcs_console = None
            rcs_result['console_action_result'] = outputs
            return rcs_result


    ############################################################################
    def fortigate_remote_console_factoryreset(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            # outputs.append(output)

            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)

            # send exec factoryreset command
            self.rcs_console.sendline('exec factoryreset')
            self.rcs_console.expect(['Do you want to continue\? \(y\/n\)'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            # send 'y' to confirm
            self.rcs_console.send('y')

            # factoryreset reboots device, and it could reboot more than once
            index = 0
            while index != 1:
                index = self.rcs_console.expect(['dummy_placeholder', ' login: ', 'System is starting', 'please wait for reboot'], timeout=1800)  
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

                if index == 2:
                    wait_for_reboot = False # reset wait_for_reboot flag
                if index == 3:
                    wait_for_reboot = True  # we received "please wait for reboot" message
                if index == 1:
                    if wait_for_reboot: # skip this login prompt
                        index = 0   # reset the index
                        continue

            rcs_result['status'] = 0
            rcs_result['changed'] = True

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.rcs_console.close()
                self.rcs_console = None
            rcs_result['console_action_result'] = outputs
            return rcs_result


    ############################################################################
    def fortigate_remote_console_erasedisk(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1    # preset rcs_outlet_port is invalid
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            # outputs.append(output)

            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)

            # send exec erase-disk command
            self.rcs_console.send('exec erase-disk ?')      # use send, not sendline here
            self.rcs_console.expect(['exec erase\-disk'])   # the 1st time expects the command echo
            self.rcs_console.expect(['exec erase\-disk'])   # the 2nd time expects the real outpout, which will prompot list of disks on your FGT system
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            # logout here now
            self.fortigate_remote_console_logout()

            # remove the empty line: if disk.strip()
            # remove the last line: output[0:-2], since the last line is cli prompt
            # remove the " (boot)": disk.strip().split(' ')[0]
            list_disk = [disk.strip().split(' ')[0] for disk in output[0:-2] if disk.strip()]

            # every erasedisk would reboot the FortiGate
            for disk in list_disk:
                self.fortigate_remote_console_login()
                self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
                self.rcs_console.expect(self.rcs_fgt_prompt)

                self.rcs_console.sendline('exec erase-disk ' + disk)
                self.rcs_console.expect('Are you sure you want to proceed\? \(y\/n\)')
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

                # send 'y' to confirm
                self.rcs_console.sendline('y')
                self.rcs_console.expect('How many times do you wish to overwrite the media\?')
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

                # erase # of times
                # erase-disk could take few hours for each round, please adjust this number
                # this version will be hardcoded to 1 time, will make it adjustable in next release
                self.rcs_console.sendline('1')

                if disk == 'SYSTEM':
                    self.rcs_console.expect('Do you want to restore the image after erasing\? \(y\/n\)')
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)
                    self.rcs_console.sendline('n')

                outputs.append('WARNING:')
                outputs.append('erase-disk starts running on ' + disk)
                outputs.append('This will permanently erase all data from the storage media.')
                outputs.append('Please do not unplug or turn off FortiGate and wait')

                start_time = datetime.datetime.now()

                if disk != 'SYSTEM':
                    # here we need to deal with some exception, sometime FortiGate doesn't like the erase-disk on data disk
                    # it will reboot and reformat the data disk, which multiple reboots could happend
                    # if we see "please wait for reboot" before the login prompt, we will skip the login prompt

                    # some remote console server also support remote power functions (poweron/poweroff/reset)
                    # for testing purpose, reboot FortiGate 20 seconds after erase-disk starts
                    # comment the follow lines in production
                    # outputs.append('for testing purpose, reboot FortiGate 20 seconds after erase-disk starts')
                    # time.sleep(20)
                    # self.rcs_outlet_reboot()

                    index = 0
                    while index != 1 and index != 2:
                        index = self.rcs_console.expect(['dummy_placeholder', 'to accept', ' login: ', 'System is starting', 'please wait for reboot'], timeout=7200)  
                        output = self.rcs_console.before.splitlines()
                        outputs.append(output)

                        if index == 3:
                            wait_for_reboot = False # reset wait_for_reboot flag
                        if index == 4:
                            wait_for_reboot = True  # we received "please wait for reboot" message
                        if index == 1 or index == 2:
                            if wait_for_reboot: # skip this login prompt
                                index = 0   # reset the index
                                continue
                            else:
                                erase_time = datetime.datetime.now() - start_time
                                minutes = int(erase_time.total_seconds() / 60)
                                outputs.append('erase-disk finish running on ' + disk)
                                outputs.append('erase-disk finish in ' + str(minutes) + ' minutes')
                                rcs_result['changed'] = True
                else:
                    self.rcs_console.expect(['You must format the boot device'], timeout=7200)  # erase-disk could take few hours, please adjust this number
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)

                    erase_time = datetime.datetime.now() - start_time
                    minutes = int(erase_time.total_seconds() / 60)
                    outputs.append('erase-disk finish running on ' + disk)
                    outputs.append('erase-disk finish in ' + str(minutes) + ' minutes')
                    rcs_result['changed'] = True

            rcs_result['status'] = 0

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.rcs_console.close()
                self.rcs_console = None
            rcs_result['console_action_result'] = outputs
            return rcs_result

    ############################################################################
    def fortigate_remote_console_diskformat(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1    # preset rcs_outlet_port is invalid
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            # outputs.append(output)

            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)

            # send exec disk list command and parse the output
            self.rcs_console.sendline('exec disk list')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            # logout for now
            self.fortigate_remote_console_logout()

            # remove the empty line: if info.strip()
            # remove the last line: output[0:-2], since the last line is cli prompt
            list_info = [info.strip() for info in output[0:-1] if info.strip()]

            disks = []
            for info in list_info:
                disk_ref_search = re.search('^Disk (\S+) +ref: +(\d+) .+', info)
                part_ref_search = re.search('^partition ref: +(\d+) .+', info)
                if disk_ref_search != None: # found new disk
                    disk = {}
                    disk['name'] = disk_ref_search.group(1)
                    disk['ref'] = disk_ref_search.group(2)
                    disk['partition'] = []
                    disks.append(disk)
                elif part_ref_search != None: # found new partition
                    disk['partition'].append(part_ref_search.group(1))
            rcs_result['disks'] = disks

            # we need to format disk without any partition
            zero_partition_disk = []
            for disk in disks:
                if len(disk['partition']) == 0:
                    zero_partition_disk.append(disk)
            
            if len(zero_partition_disk) != 0:
                # every disk format would reboot the FortiGate, we only need to format those disk without partition
                for disk in zero_partition_disk:
                    self.fortigate_remote_console_login()
                    self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
                    self.rcs_console.expect(self.rcs_fgt_prompt)

                    self.rcs_console.sendline('exec disk format ' + disk['ref'])
                    self.rcs_console.expect('Do you want to continue\? \(y\/n\)')
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)

                    # send 'y' to confirm
                    self.rcs_console.send('y')
                    # print('disk format starts running on ' + disk['name'])

                    # diskformat will reboot the device, we are now waiting for the device comes back
                    index = 0
                    while index != 1 and index != 2:
                        index = self.rcs_console.expect(['dummy_placeholder', 'to accept', ' login: ', 'System is starting', 'please wait for reboot'], timeout=7200)  
                        output = self.rcs_console.before.splitlines()
                        outputs.append(output)

                        if index == 3:
                            wait_for_reboot = False # reset wait_for_reboot flag
                        if index == 4:
                            wait_for_reboot = True  # we received "please wait for reboot" message
                        if index == 1 or index == 2:
                            if wait_for_reboot: # skip this login prompt
                                index = 0   # reset the index
                                continue
                            else:
                                # print('disk format finished on ' + disk['name'])
                                rcs_result['changed'] = True

            rcs_result['status'] = 0

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            rcs_result['console_action_result'] = outputs
            if self.rcs_console:
                self.rcs_console.close()
                self.rcs_console = None
            return rcs_result


    ############################################################################
    def fortigate_remote_console_restoreimage(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1    # preset rcs_outlet_port is invalid
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            
            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)

            # send exec factoryreset command
            self.rcs_console.sendline('exec reboot')
            self.rcs_console.expect(['Do you want to continue\? \(y\/n\)'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            # send 'y' to confirm
            self.rcs_console.send('y')

            # then on remote console port, wait/expect see the boot menu for TFTP
            # the following are FGT specific, lots of hard coded params just for my lab
            # in order to make it work for production, we need to parameterize these settings
            self.rcs_console.expect(['Press any key to display configuration menu\.\.\.'], timeout=300)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)
            time.sleep(1)

            self.rcs_console.sendline('')
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('C')  # [C]:  Configure TFTP parameters.
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            tftp_params = self.rcs_fgt_cli[0].splitlines()
            tftp_local_ip       = tftp_params[0].replace('"', '')
            tftp_local_netmask  = tftp_params[1].replace('"', '')
            tftp_local_gw       = tftp_params[2].replace('"', '')
            tftp_server_ip      = tftp_params[3].replace('"', '')
            tftp_image_file     = tftp_params[4].replace('"', '')

            self.rcs_console.send('I')  # [I]:  Set local IP address.
            # self.rcs_console.sendline('192.168.210.'+str(int((int(self.rcs_fgt_port)/100))))
            self.rcs_console.sendline(tftp_local_ip)
            time.sleep(1)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('S')  # [S]:  Set local subnet mask.
            # self.rcs_console.sendline('255.255.255.0')
            self.rcs_console.sendline(tftp_local_netmask)
            time.sleep(1)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('G')  # [G]:  Set local gateway.
            # self.rcs_console.sendline('192.168.210.1')
            self.rcs_console.sendline(tftp_local_gw)
            time.sleep(1)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('T')  # [T]:  Set remote TFTP server IP address.
            # self.rcs_console.sendline('192.168.210.252')
            self.rcs_console.sendline(tftp_server_ip)
            time.sleep(1)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('F')  # [F]:  Set firmware image file name.
            time.sleep(1)
            # self.rcs_console.sendline('/firmware/FGT_501E-v5-build1600-FORTINET.out')
            self.rcs_console.sendline(tftp_image_file)
            time.sleep(2)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('R')  # [R]:  Review TFTP parameters.
            time.sleep(1)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('Q')  # [Q]:  Quit this menu.
            time.sleep(1)
            self.rcs_console.expect(['Enter .+:'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.send('T')  # [T]:  Initiate TFTP firmware transfer.
            time.sleep(1)
            self.rcs_console.expect('Save as Default firmware\/Backup firmware\/Run image without saving:\[D\/B\/R\]\?', timeout=300)
            self.rcs_console.send('D')

            # after firmware image downloadeded and flashed, it reboots, and it could reboot more than once
            index = 0
            while index != 1:
                index = self.rcs_console.expect(['dummy_placeholder', ' login: ', 'System is starting', 'please wait for reboot'], timeout=1800)  
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

                if index == 2:
                    wait_for_reboot = False # reset wait_for_reboot flag
                if index == 3:
                    wait_for_reboot = True  # we received "please wait for reboot" message
                if index == 1:
                    if wait_for_reboot: # skip this login prompt
                        index = 0   # reset the index
                        continue

            rcs_result['status'] = 0
            rcs_result['changed'] = True

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.rcs_console.close()
                self.rcs_console = None
            rcs_result['console_action_result'] = outputs
            return rcs_result

    ############################################################################
    def fortigate_remote_console_purgedhcp(self):
        outputs = []
        rcs_result = {}
        rcs_result['status'] = 1    # preset rcs_outlet_port is invalid
        rcs_result['changed'] = False

        try:
            output = self.fortigate_remote_console_login()
            # outputs.append(output)

            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)

            self.rcs_console.sendline('config system dhcp server')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.sendline('show')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)
            show_old = output

            # send exec factoryreset command
            self.rcs_console.sendline('purge')
            self.rcs_console.expect(['Do you want to continue\? \(y\/n\)'])
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            # send 'y' to confirm
            self.rcs_console.send('y')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.sendline('show')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)
            show_new = output

            if show_old != show_new:
                rcs_result['changed'] = True

            self.rcs_console.sendline('end')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            rcs_result['status'] = 0

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.fortigate_remote_console_logout()
                self.rcs_console = None
            rcs_result['console_action_result'] = outputs
            return rcs_result

    ############################################################################
    def fortigate_remote_console_login(self):
        outputs = []
        ssh_connection_string = 'ssh %s -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -l %s -p %d' % (self.rcs_ip, self.rcs_username, self.rcs_fgt_port)

        try:
            index = 1
            while index:
                # try connect to remote console server
                # expect to see the password prompt
                self.rcs_console = pexpect.spawn(ssh_connection_string, timeout=60)
                index = self.rcs_console.expect(['assword: ', 'Connection reset by peer'], timeout=60)
                output = self.rcs_console.before.splitlines()
                outputs.append(output)

            # send remote console server password
            self.rcs_console.sendline(self.rcs_password)

            # now we should be in FortiGate context
            index = 0
            while index != 3:
                # send "enter" to FortiGate, FortiGate should spit out something, try to figure out what status/context FortiGate is in
                self.rcs_console.sendline('')
                index = self.rcs_console.expect(['dummy_placeholder', 'to accept', ' login: ', ' # ', pexpect.EOF])
                output = self.rcs_console.before.splitlines()
                outputs.append(output)
                # option#1(return 0) is not supposed to be matched
                # option#2(return 1) is when FortiGate display the pre-login banner
                # option#3(return 2) is when FortiGate display login (self.rcs_fgt_prompt)
                # option#4(return 3) is when FortiGate is already logged in
                # option#5(return 4) is something we are not sure
                if index == 1:
                    # see pre-login banner
                    self.rcs_console.sendline('a')                      # press 'a' to accept pre-login banner
                    self.rcs_console.expect(' login: ')
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)
                elif index == 2:
                    # see FortiGate login
                    self.rcs_console.sendline(self.rcs_fgt_username)    # this is username for FortiGate login
                    self.rcs_console.expect('assword: ')
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)

                    self.rcs_console.sendline(self.rcs_fgt_password)    # this is password for FortiGate login
                    login_index = self.rcs_console.expect([' # ', 'Login incorrect'])
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)
                    if login_index:                                     # Login incorrect message
                        # Failed to first login attempt, try use blank password (this could be a factory reset device)
                        self.rcs_console.sendline(self.rcs_fgt_username)# this is username for FortiGate login
                        self.rcs_console.expect('assword: ')
                        output = self.rcs_console.before.splitlines()
                        outputs.append(output)

                        self.rcs_console.sendline('')                   # try black password for FortiGate login
                        self.rcs_console.expect(' # ')
                        output = self.rcs_console.before.splitlines()
                        outputs.append(output)
                elif index == 3:                                        # with this, we want to figure out the hostname for FortiGate for better expect/match
                    hostname = self.rcs_console.before.decode('utf-8').splitlines()[-1].split(' ')[0]
                    # the first split find the last line, which contains the hostname
                    # the second split, in case FortiGate is inside configuration section or in global/vdom, FortiGate doesn't allow space in hostname
                    self.rcs_fgt_prompt = ['dummy_placeholder', hostname + ' # ', hostname + ' \(.+\) # ', ' # ', ' login: ', 'to accept']
                    prompt_index = 0
                    while prompt_index != 1:
                        self.rcs_console.sendline('')
                        prompt_index = self.rcs_console.expect(self.rcs_fgt_prompt)
                        output = self.rcs_console.before.splitlines()
                        outputs.append(output)
                        if prompt_index == 2:                           # reset FortiGate back root level (self.rcs_fgt_prompt)
                            self.rcs_console.sendline('abort')
                            prompt_index = self.rcs_console.expect(self.rcs_fgt_prompt)
                            output = self.rcs_console.before.splitlines()
                            outputs.append(output)

                            self.rcs_console.sendline('end')
                            prompt_index = self.rcs_console.expect(self.rcs_fgt_prompt)
                            output = self.rcs_console.before.splitlines()
                            outputs.append(output)

            # Another thing we need to take care of is to set console output to standard mode (default is more mode)
            self.rcs_console.sendline('config global')    # if FortiGate has VDOM enabled, if not, this will generate an message, but won't cause any problem
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.sendline('config system console')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.sendline('set output standard')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.sendline('end')
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

            self.rcs_console.sendline('end')              # end to close out if FortiGate has VDOM enabled.
            self.rcs_console.expect(self.rcs_fgt_prompt)
            output = self.rcs_console.before.splitlines()
            outputs.append(output)

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            return outputs

    ############################################################################
    def fortigate_remote_console_logout(self):
        outputs = []

        # in case FGT console is in the middle of something
        # hit enter first, then use abort to exit out if it is needed
        try:
            prompt_index = 0
            while prompt_index != 1:
                self.rcs_console.sendline('')
                prompt_index = self.rcs_console.expect(self.rcs_fgt_prompt)
                output = self.rcs_console.before.splitlines()
                outputs.append(output)
                if prompt_index == 2:           # reset FortiGate back root level (self.rcs_fgt_prompt)
                    self.rcs_console.sendline('abort')
                    prompt_index = self.rcs_console.expect(self.rcs_fgt_prompt)
                    output = self.rcs_console.before.splitlines()
                    outputs.append(output)

            # then exit to quit login
            self.rcs_console.sendline('exit')
            time.sleep(2)   # need to wait here for some reason

        except Exception as error:
            outputs.append(str(error).splitlines())

        finally:
            if self.rcs_console:
                self.rcs_console.close()
                self.rcs_console = None
            return outputs

def run_module():
    # define available arguments/parameters a user can pass to the module
    module_args = dict(
        rcs_ip=dict(required=True), # remote console server (rcs) IP address
        rcs_username=dict(type='str', required=True),   # remote console server (rcs) login username
        rcs_password=dict(type='str', required=True, no_log=True),  # remote console server (rcs) login password
        rcs_fgt_username=dict(type='str', required=True),   # FortiGate login username
        rcs_fgt_password=dict(type='str', required=True, no_log=True),  # FortiGate login password
        rcs_fgt_port=dict(type=int, required=True),   # remote console server port which maps to FortiGate console
        rcs_fgt_action=dict(choices=['cli', 'factoryreset', 'reboot', 'erasedisk', 'diskformat', 'restoreimage', 'purgedhcp'], type='str', required=False, default='cli'), # what action perform on FortiGate
        rcs_fgt_cli=dict(type='list',required=False, default=['get system status']),   # which CLI action, put list of CLI (configuration) here
    )

    # seed the result dict in the object
    # we primarily care about changed and state
    # change is if this module effectively modified the target
    # state will include any data that you want your module to pass back
    # for consumption, for example, in a subsequent task
    result = dict(
        changed=False,
        rcs_fgt_action_result={}
    )

    # the AnsibleModule object will be our abstraction working with Ansible
    # this includes instantiation, a couple of common attr would be the
    # args/params passed to the execution, as well as if the module
    # supports check mode
    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=True
    )

    # module params check
    # at least one outlet port or console port present
    if module.params['rcs_fgt_port'] is None:
        module.fail_json(msg='rcs_fgt_port needs to be specified', **result)

    _fortigate_remote_console = fortigate_remote_console(module.params['rcs_ip'], module.params['rcs_username'], module.params['rcs_password'], module.params['rcs_fgt_username'], module.params['rcs_fgt_password'], module.params['rcs_fgt_port'], module.params['rcs_fgt_cli'])
    if module.params['rcs_fgt_action'] is not None:
        # perform restore image on FortiGate, 1) reboot 2) interrupt BIOS 3) restore firmware from TFTP
        if module.params['rcs_fgt_action'] == 'restoreimage':
            console_result = _fortigate_remote_console.fortigate_remote_console_restoreimage()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_restoreimage', **result)
                return
            result['changed'] = console_result['changed']
        # perform purgedhcp on FortiGate CLI
        elif module.params['rcs_fgt_action'] == 'purgedhcp':
            console_result = _fortigate_remote_console.fortigate_remote_console_purgedhcp()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_purgedhcp', **result)
                return
            result['changed'] = console_result['changed']    # a reboot action is always has changed = True
        # perform diskformat on FortiGate CLI
        elif module.params['rcs_fgt_action'] == 'diskformat':
            console_result = _fortigate_remote_console.fortigate_remote_console_diskformat()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_diskformat', **result)
                return
            result['disks'] = console_result['disks']
            result['changed'] = console_result['changed']    # a reboot action is always has changed = True
        # perform factoryreset on FortiGate CLI
        elif module.params['rcs_fgt_action'] == 'factoryreset':
            console_result = _fortigate_remote_console.fortigate_remote_console_factoryreset()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_factoryreset', **result)
                return
            result['changed'] = console_result['changed']
        # perform reboot on FortiGate CLI
        elif module.params['rcs_fgt_action'] == 'reboot':
            console_result = _fortigate_remote_console.fortigate_remote_console_reboot()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_reboot', **result)
                return
            result['changed'] = console_result['changed']
        # perform erasedisk on FortiGate CLI
        elif module.params['rcs_fgt_action'] == 'erasedisk':
            console_result = _fortigate_remote_console.fortigate_remote_console_erasedisk()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_erasedisk', **result)
                return
            result['changed'] = console_result['changed']
        # perform configuration on FortiGate CLI (do not support configuration require interactive yet)
        elif module.params['rcs_fgt_action'] == 'cli':
            console_result = _fortigate_remote_console.fortigate_remote_console_cli()
            result['rcs_fgt_action_result'] = console_result['console_action_result']
            if console_result['status']:
                module.fail_json(msg='Something wrong with rcs_fgt_cli', **result)
                return
            result['changed'] = True

    module.exit_json(**result)

def main():
    run_module()

if __name__ == '__main__':
    main()