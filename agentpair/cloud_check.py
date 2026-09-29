"""Read-only cloud configuration and quote check. Never print credentials."""
import json
from pathlib import Path
import sys
from .ucloud import UCloudClient
from .resources import ResourceManager

def main():
    cloud=json.loads(Path(sys.argv[1]).read_text())['cloud']
    client=UCloudClient(cloud['publicKey'],cloud['privateKey'],
                        cloud['projectId'],cloud['region'])
    firewall=client.call('DescribeFirewall',FWId=cloud['firewallId'])
    images=client.call('DescribeImage',Zone=cloud['zone'],ImageType='Base',OsType='Linux',Limit=100)['ImageSet']
    image=next(i for i in images if i.get('ImageName')=='Ubuntu 22.04 64位')
    config={'Zone':cloud['zone'],'ImageId':image['ImageId'],'MachineType':'N',
            'CPU':1,'Memory':1024,'ChargeType':'Dynamic','Disks.0.Size':20,
            'Disks.0.IsBoot':'True','Disks.0.Type':'CLOUD_SSD',
            'SecurityGroupId':cloud['firewallId']}
    manager=ResourceManager(client,cloud['leaseDirectory'],
                            max_hosts=cloud.get('maxHosts',4),
                            max_hourly_cny=cloud.get('maxHourlyCNY',1.0))
    quote=manager.quote(config,{'Bandwidth':1,'ChargeType':'Dynamic',
                                'PayMode':'Bandwidth','OperatorName':'Bgp'})
    print(json.dumps({'projectId':cloud['projectId'],'region':cloud['region'],
        'firewallId':cloud['firewallId'],'firewallResources':firewall.get('DataSet',[{}])[0].get('ResourceCount'),
        'hourlyQuoteCNY':quote['hourlyCNY'],'activeLeases':len([x for x in manager.records() if x['state']=='active'])}))

if __name__=='__main__':main()
