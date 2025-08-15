***  What are we building?  ***

1) S3 bucket containing a website content
2) EC2 lauch template : intalls apache http on start and pulls content from s3 bucket automatically
3) Auto scaling group: Scales out Ec2 instances when traffic is high and sclaes in whn traffic is low

4) verification using logs and simulated traffic 

*** Architecture flow ***

Users -> load balancers -> Auto Scaling Group -> EC2 instances -> Apache server -> S3 bucket content

Use Boto3 to automate the flow 




