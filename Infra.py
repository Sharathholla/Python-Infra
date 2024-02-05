import boto3
import logging
from botocore.exceptions import ClientError
logger = logging.getLogger()
logging.basicConfig(level=logging.INFO, format='%(asctime)s: %(levelname)s: %(message)s')
import base64
import time

vpc_identifier = ''
sg_id = ''
target_group_ARN = ''
igw = ''
ec2_client = boto3.client('ec2', region_name='ap-south-1')


def create_vpc(vpc_name, cidr, public_subnet1_cidr, public_subnet1_az, public_subnet2_cidr, public_subnet2_az,  private_subnet_cidr):
    response = ec2_client.create_vpc(CidrBlock=cidr)
    vpc_id = response['Vpc']['VpcId']
    ec2_client.create_tags(Resources=[vpc_id], Tags=[{'Key': 'Name', 'Value': vpc_name}, {'Key': 'Product', 'Value': 'Challenge'}])
    print('VPC Created: ', vpc_id)
    print('Creating Public Subnet1')
    create_subnet(vpc_id, public_subnet1_cidr, public_subnet1_az, True)
    print('Creating Public Subnet2')
    create_subnet(vpc_id, public_subnet2_cidr, public_subnet2_az, True)
    print('Creating Private Subnet')
    create_subnet(vpc_id, private_subnet_cidr, public_subnet1_az, False)
    print('Creating Internet Gateway')
    internet_gateway = ec2_client.create_internet_gateway()
    internet_gateway_attach = ec2_client.attach_internet_gateway(
        InternetGatewayId=internet_gateway['InternetGateway']['InternetGatewayId'],
        VpcId=vpc_id
    )
    print('Created Internet Gateway: ', internet_gateway['InternetGateway']['InternetGatewayId'])
    global igw
    igw = internet_gateway['InternetGateway']['InternetGatewayId']
    ec2_client.create_tags(
        Resources=[igw],
        Tags=[{'Key': 'Name', 'Value': 'Qube-IGW'}, {'Key': 'Product', 'Value': 'Challenge'}]
    )
    global vpc_identifier
    vpc_identifier = vpc_id
    return vpc_id


def create_rt_association(vpc_id, subnet_id, igw):
    if subnet_id != '':
        print('Creating Route Table')
        route_table = ec2_client.create_route_table(VpcId=vpc_id)
        route_table_id = route_table['RouteTable']['RouteTableId']
        ec2_client.create_tags(
            Resources=[route_table_id],
            Tags=[{'Key': 'Name', 'Value': 'Qube-RT'}, {'Key': 'Product', 'Value': 'Challenge'}]
        )
        route = ec2_client.create_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock='0.0.0.0/0',
            GatewayId=igw
        )
        ec2_client.associate_route_table(
            RouteTableId=route_table_id,
            SubnetId=subnet_id
        )
        print(subnet_id, 'Added in route table.')
    else:
        print('Skipping route table creation')


def create_subnet(vpc_id, cidr, az, is_public):
    response = ec2_client.create_subnet(VpcId=vpc_id, CidrBlock=cidr, AvailabilityZone=az)
    subnet_id = response['Subnet']['SubnetId']
    if is_public:
        ec2_client.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={'Value': True})
        print("subnet created: ", subnet_id)
    ec2_client.create_tags(
        Resources=[subnet_id],
        Tags=[{'Key': 'Name', 'Value': 'Qube-Subnet'}, {'Key': 'Product', 'Value': 'Challenge'}]
    )
    return subnet_id


def create_sg_and_launch_template(vpc_identifier):
    print('Creating SG')
    security_group_response = ec2_client.create_security_group(
        GroupName='Qube-sg',
        Description='Security group for web instances',
        VpcId=vpc_identifier
    )
    security_group_id = security_group_response['GroupId']
    global sg_id
    sg_id = security_group_id
    ec2_client.create_tags(
        Resources=[security_group_id],
        Tags=[{'Key': 'Name', 'Value': 'Qube-Sg'}, {'Key': 'Product', 'Value': 'challenge'}]
    )

    ec2_client.authorize_security_group_ingress(
        GroupId=security_group_id,
        IpPermissions=[
            {
                'IpProtocol': 'tcp',
                'FromPort': 80,
                'ToPort': 80,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
            },
            {
                'IpProtocol': 'tcp',
                'FromPort': 22,
                'ToPort': 22,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
            }
        ]
    )
    print('security group created: ', security_group_id)
    # Create launch template
    print('Creating Launch Template')
    user_data = '''#!/bin/bash
    sudo yum update -y
    sudo yum install httpd -y
    sudo service httpd start
    sudo touch /opt/launchfile
    sudo echo "helloworld" > /opt/launchfile
    sudo echo "Hello world python" > /var/www/html/index.html
    '''
    encoded_user_data = base64.b64encode(user_data.encode('ascii')).decode('ascii')
    launch_template = ec2_client.create_launch_template(
        LaunchTemplateName='Qube-launch-template',
        LaunchTemplateData={
            # Can change the below conifguration as required
            'ImageId': 'ami-0d63de463e6604d0a',
            'InstanceType': 't2.micro',
            'KeyName': 'Sharathpvtkey',
            'UserData': encoded_user_data,
            'SecurityGroupIds': [security_group_id],
            'TagSpecifications': [
                {
                    'ResourceType': 'instance',
                    'Tags': [{'Key': 'Product', 'Value': 'challenge'}]
                }
            ]
        }
    )

    launch_template_id = launch_template['LaunchTemplate']['LaunchTemplateId']
    ec2_client.create_tags(
        Resources=[launch_template_id],
        Tags=[{'Key': 'Name', 'Value': 'Qube-LT'}, {'Key': 'Product', 'Value': 'Challenge'}]
    )
    print("Launch template created")


def create_asg_lt_tg(vpc_identifier, target_group_name, Subnets):
    if Subnets != '':
        elb = boto3.client('elbv2', region_name='ap-south-1')
        autoscaling_client = boto3.client('autoscaling', region_name='ap-south-1')

        # Create the target group
        print('Creating TG')
        try:
            response = elb.create_target_group(
                Name=target_group_name,
                Protocol='HTTP',
                Port=80,
                VpcId=vpc_identifier,
            )
            target_group = response["TargetGroups"][0]
            target_group_arn = response["TargetGroups"][0]['TargetGroupArn']
            print('Target Group Created: ', target_group, 'ARN: ', target_group_arn)
            elb.add_tags(
                ResourceArns=[target_group_arn],
                Tags=[{'Key': 'Name', 'Value': 'Qube-TG'}, {'Key': 'Product', 'Value': 'Challenge'}]
            )

            # Creating ASG
            asg_response = autoscaling_client.create_auto_scaling_group(
                AutoScalingGroupName='Qube-ASG',
                LaunchTemplate={
                    'LaunchTemplateName': 'Qube-launch-template',
                    'Version': '$Latest'
                },
                MinSize=1,
                MaxSize=3,
                DesiredCapacity=1,
                VPCZoneIdentifier=Subnets[0],
                TargetGroupARNs=[target_group_arn]
            )
            autoscaling_client.create_or_update_tags(Tags=[
                {
                    'ResourceId': 'Qube-ASG',
                    'ResourceType': 'auto-scaling-group',
                    'Key': 'Product',
                    'Value': 'Challenge',
                    'PropagateAtLaunch': True
                }
            ])
            print('ASG Created')
            global target_group_ARN
            target_group_ARN = target_group_arn
            print('Target Group Created')
        except ClientError:
            logger.exception(f'Could not create target group')
            raise
        else:
            return target_group_ARN
    else:
        print('Skipping as already created')


def create_lb(Subnets, sg_id, target_group_ARN):
    elb = boto3.client('elbv2', region_name='ap-south-1')
    print("Load balancer does not exist")
    response = elb.create_load_balancer(
        Name='Qube-load-balancer',
        Subnets=Subnets,
        SecurityGroups=[sg_id],
        Tags=[{
            'Key': 'Product',
            'Value': 'challenge'
        }]
    )
    load_balancer_arn = response['LoadBalancers'][0]['LoadBalancerArn']
    print('created load balancer and its arn is ', load_balancer_arn)
    waiter = elb.get_waiter('load_balancer_available')
    listener_response = elb.create_listener(
        LoadBalancerArn=load_balancer_arn,
        Protocol='HTTP', Port=80,
        DefaultActions=[
            {'Type': 'forward', 'TargetGroupArn': target_group_ARN}
        ]
    )
    listener_arn = listener_response['Listeners'][0]['ListenerArn']
    print(f"Listener ARN: {listener_arn}")
    print('creating listener rule for alb')
    time.sleep(180)
    elb.create_rule(
        ListenerArn=listener_arn,
        Conditions=[{
            'Field': 'path-pattern',
            'PathPatternConfig': {'Values': ['/worldsogood']}
        }],
        Priority=1,
        Actions=[{
            'Type': 'forward',
            'TargetGroupArn': target_group_ARN
        }]
    )
    print('created listener rule for alb')
    return response['LoadBalancers'][0]['LoadBalancerArn']


def vpc_validation(vpc_name, cidr, public_subnet1_cidr, public_subnet1_az, public_subnet2_cidr, public_subnet2_az,  private_subnet_cidr):
    """
        VPC VALIDATION STARTED
    """
    try:
        response = ec2_client.describe_vpcs(
            Filters=[
                {
                    'Name': 'tag:Name',
                    'Values': [
                        vpc_name,
                    ]
                },
                {
                    'Name': 'cidr-block-association.cidr-block',
                    'Values': [
                        cidr,
                    ]
                },
            ]
        )
        resp = response['Vpcs']
        if resp:
            print(resp)
            print('There is already a vpc with the config provided')
            vpc_id = response['Vpcs'][0]['VpcId']
            global vpc_identifier
            vpc_identifier = vpc_id
        else:
            print('No vpcs found, so creating vpc')
            create_vpc(vpc_name, cidr, public_subnet1_cidr, public_subnet1_az, public_subnet2_cidr, public_subnet2_az,  private_subnet_cidr)

    except ClientError:
        logger.exception(f'Could not create VPC {vpc_name}.')
        raise


if __name__ == '__main__':
    print('Enter the VPC Name. For eg Checking_VPC2')
    vpc_name = input()
    print('Enter the CIDR block. For eg 10.4.0.0/16')
    cidr = input()
    print('Enter the CIDR block for public subnet1. For eg 10.4.16.0/20')
    public_subnet1_cidr = input()
    print('Enter the az public subnet1. For eg ap-south-1a')
    public_subnet1_az = input()
    print('Enter the CIDR block for public subnet2. For eg 10.4.32.0/20')
    public_subnet2_cidr = input()
    print('Enter the az public subnet2. For eg ap-south-1b')
    public_subnet2_az = input()
    print('Enter the CIDR block for private subnet. For eg 10.4.48.0/20')
    private_subnet_cidr = input()
    VPC_Validation = vpc_validation(vpc_name, cidr, public_subnet1_cidr, public_subnet1_az, public_subnet2_cidr, public_subnet2_az, private_subnet_cidr)
    Subnets = []
    print('Is subnets already created? please type yes if created and no if not')
    subnet_created=input()
    if subnet_created == 'no':
        print('Enter the First public subnet id created where the ASG needs to be created and for which igw should be attached If not created earlier')
        public_subnet1 = input()
        create_rt_association(vpc_identifier, public_subnet1, igw)
        Subnets.append(public_subnet1)
        print('Enter the Second public subnet id created where the ASG needs to be created and for which igw should be attached.If not  created earlier')
        public_subnet2 = input()
        create_rt_association(vpc_identifier, public_subnet2, igw)
        Subnets.append(public_subnet2)
    else:
        print('Enter the First public subnet id 1 created earlier')
        public_subnet1 = input()
        Subnets.append(public_subnet1)
        print('Enter the Second public subnet id 2 created earlier')
        public_subnet2 = input()
        Subnets.append(public_subnet2)
    print('Creating security group')
    create_sg_and_launch_template(vpc_identifier)
    target_group_name = 'Qube-TG1'
    create_asg_lt_tg(vpc_identifier, target_group_name, Subnets)
    create_lb(Subnets, sg_id, target_group_ARN)

