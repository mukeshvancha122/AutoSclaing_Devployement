import boto3 # type: ignore
import os
import time

REGION = os.getenv("REGION", "us-east-1")
PROJECT = os.getenv("PROJECT", "AutoScalingApp")
session = boto3.Session(region_name=REGION)

ec2 = session.client("ec2")
elbv2 = session.client("elbv2")
asg = session.client("autoscaling")

def wait_until_ld_deleted(lb_arn, timeout=300):
    start=time.time()
    while time.time() - start < timeout:
        lbs=elbv2.describe_load_balancers()["LoadBalancers"]
        if not any(lb["LoadBalancerArn"] == lb_arn for lb in lbs):
            print(f"Load Balancer {lb_arn} deleted successfully.")
            return
        time.sleep(5)

def main():
    asg_name = f"{PROJECT}-asg"
    lt_name = f"{PROJECT}-lt"
    alb_name = f"{PROJECT}-alb"
    tg_name = f"{PROJECT}-tg"
    alb_sg_name = f"{PROJECT}-alb-sg"
    ec2_sg_name = f"{PROJECT}-ec2-sg"

    # scaling in and deleting in Auto Scaling Group
    try:
        asg.update_auto_scaling_group(AutoScalingGroupName=asg_name, MinSize=0, DesiredCapacity=0)
    except Exception:
        pass
    time.sleep(10)
    try:
        asg.delete_auto_scaling_group(AutoScalingGroupName=asg_name, ForceDelete=True)
    except Exception:
        pass

    # deleting listeners + ALB 
    lb_arn= None 
    try:
        lbs = elbv2.describe_load_balancers()["LoadBalancers"]
        for lb in lbs:
            if lb["LoadBalancerName"] == alb_name:
                lb_arn = lb["LoadBalancerArn"]
                listeners = elbv2.describe_listeners(LoadBalancerArn=lb_arn)["Listeners"]
                for lst in listeners:
                    try:
                        elbv2.delete_listener(ListenerArn=lst["ListenerArn"])
                    except Exception:
                        pass
                elbv2.delete_load_balancer(LoadBalancerArn=lb_arn)
                break
    except Exception:
        pass

    if lb_arn:
        wait_until_ld_deleted(lb_arn)

    # Delete Target Group
    try:
        tgs = elbv2.describe_target_groups()["TargetGroups"]
        for tg in tgs:
            if tg["TargetGroupName"] == tg_name:
                try:
                    elbv2.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
                except Exception:
                    pass
    except Exception:
        pass

    # Delete Launch Template
    try:
        ec2.delete_launch_template(LaunchTemplateName=lt_name)
    except Exception:
        pass

    # Delete Security Groups (revoke rules first)
    try:
        sgs = ec2.describe_security_groups()["SecurityGroups"]
        for sg in sgs:
            if sg["GroupName"] in [alb_sg_name, ec2_sg_name]:
                try:
                    if sg.get("IpPermissions"):
                        ec2.revoke_security_group_ingress(GroupId=sg["GroupId"], IpPermissions=sg["IpPermissions"])
                    if sg.get("IpPermissionsEgress"):
                        ec2.revoke_security_group_egress(GroupId=sg["GroupId"], IpPermissions=sg["IpPermissionsEgress"])
                    ec2.delete_security_group(GroupId=sg["GroupId"])
                except Exception:
                    pass
    except Exception:
        pass

if __name__ == "__main__":
    main()