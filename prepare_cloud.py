"""Validate cloud identity/prices and create a dedicated Driver SSH firewall."""
import getpass
import json
from agentpair.ucloud import UCloudClient

def main():
    public=getpass.getpass('UCloud API ID (hidden): ')
    secret=getpass.getpass('UCloud API secret (hidden): ')
    client=UCloudClient(public,secret,'org-2qgt5t','cn-bj2')
    images=client.call('DescribeImage',Zone='cn-bj2-04',ImageType='Base',OsType='Linux',Limit=100)['ImageSet']
    image=next(i for i in images if i.get('ImageName')=='Ubuntu 22.04 64位')
    config={'Zone':'cn-bj2-04','ImageId':image['ImageId'],'MachineType':'N','CPU':1,
            'Memory':1024,'ChargeType':'Dynamic','Disks.0.Size':20,
            'Disks.0.IsBoot':'True','Disks.0.Type':'CLOUD_SSD'}
    host=client.call('GetUHostInstancePrice',**dict(config,Count=1))
    eip=client.call('GetEIPPrice',OperatorName='Bgp',Bandwidth=1,
                    ChargeType='Dynamic',PayMode='Bandwidth')
    def rate(items):return next(float(x['Price']) for x in items['PriceSet'] if x['ChargeType']=='Dynamic')
    hourly=rate(host)+rate(eip)
    if hourly>1.0: raise RuntimeError('Driver price exceeds configured hourly ceiling')
    result=client.call('CreateFirewall',Name='agentpair-driver-ssh-20260929',
        **{'Rule.0':'TCP|22|50.118.187.180/32|ACCEPT|HIGH|NavigatorSSH'})
    print(json.dumps({'projectId':'org-2qgt5t','region':'cn-bj2','zone':'cn-bj2-04',
        'imageId':image['ImageId'],'hourlyQuoteCNY':hourly,'firewallId':result['FWId']}))

if __name__=='__main__':main()
