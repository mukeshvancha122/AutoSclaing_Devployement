import base64
import boto3
import json
import os
import sys

REGION = os.getenv("REGION", "us-east-1")
AMI_ID = os.getenv("AMI_ID", "")              # leave empty to auto-select Amazon Linux 2023 x86_64
INSTANCE_TYPE = os.getenv("INSTANCE_TYPE", "t3.micro")
KEY_NAME = os.getenv("KEY_NAME", "")          # optional
BUCKET_NAME = os.getenv("BUCKET_NAME", "saimukeshreddyvanchas3")
BUCKET_PREFIX = os.getenv("BUCKET_PREFIX", "webroot")
PROJECT = os.getenv("PROJECT", "AutoScalingApp")

#  minimum and maximum number of instances in the Auto Scaling Group
ASG_MIN = int(os.getenv("ASG_MIN", "1"))
ASG_MAX = int(os.getenv("ASG_MAX", "4"))
ASG_DESIRED = int(os.getenv("ASG_DESIRED", "1"))

session = boto3.Session(region_name=REGION)
ec2 = session.client("ec2")
asg = session.client("autoscaling")
iam = session.client("iam")
s3 = session.client("s3")
s3 = session.client("s3")
elbv2 = session.client("elbv2")

def get_default_vpc_subnets():
    vpcs=ec2.describe_vpcs(Filters=[{
        'Name': 'isDefault',
        'Values': ['true']
    }])["Vpcs"]
    if not vpcs:
        raise Exception("No default VPC found")
    vpcs_id=vpcs[0]["VpcId"]
    subnets=ec2.describe_subnets(Filters=[{
        'Name': 'vpc-id',
        'Values': [vpcs_id]
    }])["Subnets"]
    subnet_id=[s["SubnetId"] for s in subnets]
    if len(subnets) == 0:
        raise Exception("No subnets found in the default VPC")
    return vpcs_id, subnet_id

def latest_ami():
    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name":"name","Values":["al2023-ami-2023.*-x86_64"]},
            {"Name":"architecture","Values":["x86_64"]},
            {"Name":"state","Values":["available"]},
        ]
    )["Images"]
    if not images:
        raise Exception("No Amazon Linux 2023 AMI found")
    images.sort(key=lambda x: x["CreationDate"], reverse=True)
    return images[0]["ImageId"]

def ensure_instance_profile(name="AutoScalingAppInstanceProfile"):
    try:
        iam.get_instance_profile(InstanceProfileName=name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_instance_profile(InstanceProfileName=name)

    # Ensure role exists
    role_name = f"{name}-role"
    assume = {
        "Version":"2012-10-17",
        "Statement":[
            {"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}
        ]
    }
    try:
        iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(assume))

    # Attach managed policies
    for policy in [
        "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
        "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
    ]:
        try:
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy)
        except Exception:
            pass
    # Add role to instance profile (idempotent)
    try:
        iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=role_name)
    except Exception:
        pass

    return name

def main():
    vpc_id, subnet_ids = get_default_vpc_subnets()
    print(f"Using VPC {vpc_id} and subnets {subnet_ids}")
    ami=AMI_ID or latest_ami()

    here = os.path.dirname(__file__)
    with open(os.path.join(here, "user_data.sh"), "r") as f:
        ud = f.read()
    ud = (ud.replace("your-private-content-bucket", BUCKET_NAME)
            .replace("webroot", BUCKET_PREFIX)
            .replace("us-east-1", REGION))
    user_data_b64 = base64.b64encode(ud.encode("utf-8")).decode("utf-8")

    iprofile = ensure_instance_profile(f"{PROJECT}-instance-profile")

    # Security Group for EC2
    try:
        ec2_sg = ec2.create_security_group(
            GroupName=f"{PROJECT}-ec2-sg",
            Description="EC2 SG for ASG instances",
            VpcId=vpc_id
        )["GroupId"]
        ec2.authorize_security_group_egress(GroupId=ec2_sg, IpPermissions=[{
            "IpProtocol":"-1",
            "IpRanges":[{"CidrIp":"0.0.0.0/0"}]
        }])
    except Exception:
        # Reuse if it already exists
        ec2_sg = ec2.describe_security_groups(
            Filters=[
                {"Name":"group-name","Values":[f"{PROJECT}-ec2-sg"]},
                {"Name":"vpc-id","Values":[vpc_id]}
            ])["SecurityGroups"][0]["GroupId"]

    # Launch Template
    lt_name = f"{PROJECT}-lt"
    lt_data = {
        "ImageId": ami,
        "InstanceType": INSTANCE_TYPE,
        "IamInstanceProfile": {"Name": iprofile},
        "SecurityGroupIds": [ec2_sg],
        "UserData": user_data_b64
    }
    if KEY_NAME:
        lt_data["KeyName"] = KEY_NAME
    try:
        ec2.create_launch_template(LaunchTemplateName=lt_name, LaunchTemplateData=lt_data)
    except Exception:
        # If exists, we’re fine using $Latest
        pass

    # Target Group
    tg = elbv2.create_target_group(
        Name=f"{PROJECT}-tg",
        Protocol="HTTP",
        Port=80,
        VpcId=vpc_id,
        HealthCheckProtocol="HTTP",
        HealthCheckPath="/health",
        TargetType="instance"
    )["TargetGroups"][0]

    # ALB Security Group
    try:
        alb_sg = ec2.create_security_group(
            GroupName=f"{PROJECT}-alb-sg",
            Description="ALB SG",
            VpcId=vpc_id
        )["GroupId"]
        ec2.authorize_security_group_ingress(
            GroupId=alb_sg,
            IpPermissions=[{
                "IpProtocol":"tcp",
                "FromPort":80,
                "ToPort":80,
                "IpRanges":[{"CidrIp":"0.0.0.0/0"}]
            }]
        )
    except Exception:
        alb_sg = ec2.describe_security_groups(
            Filters=[
                {"Name":"group-name","Values":[f"{PROJECT}-alb-sg"]},
                {"Name":"vpc-id","Values":[vpc_id]}
            ])["SecurityGroups"][0]["GroupId"]

    # ALB
    subnets_for_alb = subnet_ids[:2]
    alb = elbv2.create_load_balancer(
        Name=f"{PROJECT}-alb",
        Subnets=subnets_for_alb,
        SecurityGroups=[alb_sg],
        Scheme="internet-facing",
        Type="application",
        IpAddressType="ipv4"
    )["LoadBalancers"][0]
    alb_arn = alb["LoadBalancerArn"]
    alb_dns = alb["DNSName"]

    # Listener → forward to TG
    elbv2.create_listener(
        LoadBalancerArn=alb_arn,
        Protocol="HTTP",
        Port=80,
        DefaultActions=[{"Type":"forward","TargetGroupArn":tg["TargetGroupArn"]}]
    )

    # Allow ALB → EC2 on port 80
    try:
        ec2.authorize_security_group_ingress(
            GroupId=ec2_sg,
            IpPermissions=[{
                "IpProtocol":"tcp",
                "FromPort":80,
                "ToPort":80,
                "UserIdGroupPairs":[{"GroupId":alb_sg}]
            }]
        )
    except Exception:
        pass

    # Auto Scaling Group
    asg_name = f"{PROJECT}-asg"
    asg.create_auto_scaling_group(
        AutoScalingGroupName=asg_name,
        MinSize=ASG_MIN,
        MaxSize=ASG_MAX,
        DesiredCapacity=ASG_DESIRED,
        VPCZoneIdentifier=",".join(subnet_ids[:2]),
        TargetGroupARNs=[tg["TargetGroupArn"]],
        LaunchTemplate={"LaunchTemplateName": lt_name, "Version": "$Latest"},
        HealthCheckType="ELB",           # use ELB health for replacement if unhealthy
        HealthCheckGracePeriod=180
    )

    # Target tracking scaling policy: average CPU 50%
    asg.put_scaling_policy(
        AutoScalingGroupName=asg_name,
        PolicyName=f"{PROJECT}-cpu50",
        PolicyType="TargetTrackingScaling",
        TargetTrackingConfiguration={
            "PredefinedMetricSpecification": {"PredefinedMetricType": "ASGAverageCPUUtilization"},
            "TargetValue": 50.0,
            "DisableScaleIn": False
        }
    )

    print(json.dumps({
        "alb_dns": alb_dns,
        "asg": asg_name,
        "target_group": tg["TargetGroupArn"],
        "launch_template": lt_name,
        "security_groups": {"alb_sg": alb_sg, "ec2_sg": ec2_sg}
    }, indent=2))

if __name__ == "__main__":
    main()