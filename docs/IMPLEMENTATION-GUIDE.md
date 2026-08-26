# Implementation Guide

Step-by-step build of the [architecture](../README.md) using the AWS Management
Console. Every resource is created explicitly rather than through a wizard, so you can
see how the pieces connect.

Budget about two hours end to end. The RDS instance takes 10–15 minutes to become
available on its own, so start Phase 3 and continue with Phase 4 while it provisions.

## Before you start

You need an AWS account with administrator access, used through an **IAM user or an
IAM Identity Center login — not the root account**. You also need a Spring Boot
application packaged as an executable JAR. Requirements for the app are in
[Appendix A](#appendix-a--what-the-spring-boot-app-must-do).

Pick one region and stay in it for the whole build. This guide uses **us-east-1**
with AZs `us-east-1a` and `us-east-1b`. Resources in different regions cannot see each
other, and the single most common failure in a build like this is creating something
in the wrong region without noticing. Check the region selector in the console's top
right before every phase.

> **Cost warning.** This architecture is not free-tier eligible. Multi-AZ RDS,
> two NAT Gateways, and an Application Load Balancer together run roughly
> $130–170/month if left running. Cost-reduction options are noted inline, and
> [Phase 10](#phase-10--teardown) tells you how to delete everything cleanly.

The build order below follows the dependency chain — each phase creates something the
next one references, so working out of order means backtracking.

---

## Phase 1 — Network foundation

### 1.1 Create the VPC

Go to **VPC → Your VPCs → Create VPC** and choose **VPC only**.

| Field | Value |
| --- | --- |
| Name tag | `ha-app-vpc` |
| IPv4 CIDR | `10.0.0.0/16` |
| Tenancy | Default |

After it is created, select it and use **Actions → Edit VPC settings** to turn on
both **Enable DNS resolution** and **Enable DNS hostnames**. DNS hostnames are off by
default on a manually created VPC, and without them your instances cannot resolve the
RDS endpoint — a failure that surfaces much later as a confusing connection timeout.

### 1.2 Create six subnets

Go to **Subnets → Create subnet**, select `ha-app-vpc`, and add all six in one pass
using **Add new subnet**:

| Name | Availability Zone | CIDR | Purpose |
| --- | --- | --- | --- |
| `public-a` | us-east-1a | `10.0.1.0/24` | Load balancer |
| `public-b` | us-east-1b | `10.0.2.0/24` | Load balancer |
| `app-a` | us-east-1a | `10.0.11.0/24` | EC2 instances |
| `app-b` | us-east-1b | `10.0.12.0/24` | EC2 instances |
| `db-a` | us-east-1a | `10.0.21.0/24` | RDS |
| `db-b` | us-east-1b | `10.0.22.0/24` | RDS |

Then select `public-a` and `public-b` in turn and use **Actions → Edit subnet
settings → Enable auto-assign public IPv4 address**. Only these two get public IPs.

### 1.3 Create and attach the Internet Gateway

Go to **Internet gateways → Create internet gateway**, name it `ha-app-igw`, create
it, then use **Actions → Attach to VPC** and select `ha-app-vpc`. An unattached
gateway does nothing and gives no warning, so confirm the state reads **Attached**.

### 1.4 Create NAT Gateways

Go to **NAT gateways → Create NAT gateway**:

| Field | Value |
| --- | --- |
| Name | `ha-app-nat-a` |
| Subnet | `public-a` |
| Connectivity type | Public |
| Elastic IP | Click **Allocate Elastic IP** |

Repeat for `ha-app-nat-b` in `public-b`. The NAT Gateway must live in a *public*
subnet even though it serves private ones — placing it in a private subnet is a common
mistake that produces a resource that cannot route anywhere.

Two NAT Gateways cost about $65/month combined. **To halve that**, create only
`ha-app-nat-a` and point both app route tables at it — accepting that an AZ-A outage
cuts outbound internet for AZ-B instances. Inbound traffic through the load balancer
keeps working either way, so for a demo environment this is a reasonable trade.

### 1.5 Create route tables

Go to **Route tables** and create four, associating subnets under the **Subnet
associations** tab of each:

| Route table | Route to add | Associate with |
| --- | --- | --- |
| `public-rt` | `0.0.0.0/0` → `ha-app-igw` | `public-a`, `public-b` |
| `app-rt-a` | `0.0.0.0/0` → `ha-app-nat-a` | `app-a` |
| `app-rt-b` | `0.0.0.0/0` → `ha-app-nat-b` | `app-b` |
| `db-rt` | none — local only | `db-a`, `db-b` |

The app tier gets one route table per AZ deliberately: each must exit through the NAT
Gateway in its *own* AZ. Sharing a single table would send AZ-B traffic across the AZ
boundary, adding latency, cross-AZ data charges, and a dependency on the very AZ you
are trying to be independent of.

The `db-rt` table has no internet route at all. Keeping the database subnets on their
own table also protects them from inheriting a default route if someone later edits
the main route table.

> **Checkpoint.** Each of the six subnets should appear under exactly one route table's
> associations. Any subnet still on the main route table was missed.

---

## Phase 2 — Security groups

Create these in order, because each references the one before it. Go to
**VPC → Security groups → Create security group**, and select `ha-app-vpc` each time.

**`sg-alb`** — description "ALB from internet". Inbound rules:

| Type | Port | Source |
| --- | --- | --- |
| HTTP | 80 | `0.0.0.0/0` |
| HTTPS | 443 | `0.0.0.0/0` |

**`sg-app`** — description "App from ALB". One inbound rule: **Custom TCP**, port
`8080`, and in the source box type `sg-` and pick **`sg-alb`** from the dropdown.

**`sg-db`** — description "MySQL from app". One inbound rule: **MySQL/Aurora**, port
`3306`, source **`sg-app`**.

Leave the default outbound rule (all traffic) on all three.

The important detail is that the sources are *security group IDs*, not CIDR ranges.
This makes the rules identity-based: any instance carrying `sg-app` can reach the
database, and nothing else can, regardless of IP address. Since Auto Scaling replaces
instances with new addresses constantly, an IP-based rule would need updating on every
scaling event.

---

## Phase 3 — RDS MySQL Multi-AZ

### 3.1 Create a DB subnet group

Go to **RDS → Subnet groups → Create DB subnet group**. Name it `ha-app-db-subnets`,
select `ha-app-vpc`, then add AZs `us-east-1a` and `us-east-1b` and pick subnets
`db-a` and `db-b`. Verify exactly two subnets are listed — the console will happily
let you add the wrong ones.

### 3.2 Create the database

Go to **RDS → Databases → Create database** and choose **Standard create**, engine
**MySQL**, latest 8.0 version.

| Section | Setting | Value |
| --- | --- | --- |
| Templates | | Production |
| Availability | Deployment | **Multi-AZ DB instance** |
| Settings | DB identifier | `ha-app-db` |
| Credentials | Management | **Managed in AWS Secrets Manager** |
| Instance | Class | `db.t3.micro` |
| Storage | Type / size | gp3, 20 GiB, autoscaling on |
| Connectivity | VPC | `ha-app-vpc` |
| Connectivity | Subnet group | `ha-app-db-subnets` |
| Connectivity | Public access | **No** |
| Connectivity | Security group | `sg-db` (remove `default`) |
| Additional | Initial database name | `appdb` |
| Additional | Backup retention | 7 days |
| Additional | Encryption | Enabled |

Two choices deserve care. Under Availability, pick **Multi-AZ DB instance
deployment (2 instances)** — the similarly named "Multi-AZ DB *cluster*" is a
three-node deployment with readable standbys that costs considerably more and is not
what this architecture describes. And **Initial database name** is easy to miss; if
you leave it blank, RDS creates no database at all and your application fails to
start with an "unknown database" error.

Letting RDS manage credentials in Secrets Manager means the password is generated,
never displayed, and never typed into a config file. Note the secret's name from the
**Configuration** tab once the instance is up.

Creation takes 10–15 minutes. **Continue to Phase 4 while you wait.** When it reaches
**Available**, copy the **Endpoint** from the Connectivity & security tab — it looks
like `ha-app-db.abcdefghijkl.us-east-1.rds.amazonaws.com`. This is the DNS name that
survives failover, and it is what your application connects to. Never connect to an
underlying instance address directly.

---

## Phase 4 — Application artifact and IAM role

### 4.1 Upload the JAR to S3

Create a bucket (**S3 → Create bucket**) with a globally unique name such as
`ha-app-artifacts-<your-initials>-<random>`, in the same region, with default settings
— **Block all public access** stays on, since instances read the JAR through IAM, not
public URLs. Upload your `app.jar` to the bucket root.

### 4.2 Create the EC2 IAM role

Go to **IAM → Roles → Create role**, trusted entity **AWS service**, use case **EC2**.
Attach the managed policy **`AmazonSSMManagedInstanceCore`**, which enables shell
access through Session Manager so you do not need SSH, a key pair, or a bastion host.

Name the role `ha-app-ec2-role`. Then open it, and under **Add permissions → Create
inline policy**, use the JSON editor with the following, substituting your bucket name
and the secret ARN from Phase 3:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::ha-app-artifacts-CHANGE-ME/*"
    },
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:us-east-1:CHANGE-ME:secret:rds!db-CHANGE-ME"
    }
  ]
}
```

Name it `ha-app-artifact-access`. Scoping these to specific resources rather than `*`
means a compromised instance can read one JAR and one secret, not your entire account.

---

## Phase 5 — Launch template

The launch template is the blueprint the Auto Scaling Group stamps out. Go to
**EC2 → Launch templates → Create launch template**.

| Field | Value |
| --- | --- |
| Name | `ha-app-lt` |
| AMI | Amazon Linux 2023 (64-bit x86) |
| Instance type | `t3.micro` |
| Key pair | **Do not include** |
| Subnet | **Don't include in launch template** |
| Security group | `sg-app` |
| Advanced → IAM instance profile | `ha-app-ec2-role` |
| Advanced → Metadata version | V2 only (token required) |

Leave the subnet unset on purpose — the Auto Scaling Group supplies it, and that is
what lets one template produce instances in both AZs. Pinning a subnet here would
defeat the multi-AZ design.

Expand **Advanced details**, scroll to **User data**, and paste the contents of
[`../scripts/user-data.sh`](../scripts/user-data.sh) with the five values in its
CONFIG block replaced: your region, the S3 URI of the JAR, the Secrets Manager secret
name, the RDS endpoint from Phase 3, and the database name `appdb`.

That script installs Java 17, downloads the JAR, reads the database username and
password from Secrets Manager at boot, writes them to a root-owned environment file,
and runs the app as a `systemd` service under an unprivileged user. Because the
credentials are fetched at boot rather than baked into the image, rotating the secret
requires only an instance refresh.

---

## Phase 6 — Target group and load balancer

### 6.1 Create the target group

Go to **EC2 → Target groups → Create target group**, type **Instances**.

| Field | Value |
| --- | --- |
| Name | `ha-app-tg` |
| Protocol / port | HTTP `8080` |
| VPC | `ha-app-vpc` |
| Health check path | `/actuator/health` |
| Healthy / unhealthy threshold | 2 / 2 |
| Interval / timeout | 15s / 5s |

**Do not register any instances** on the next screen — click through and create the
group empty. The Auto Scaling Group registers and deregisters instances automatically
in Phase 7, and manually added instances would not be managed by it.

The health check settings determine how fast a broken instance is pulled from
rotation: two failed checks 15 seconds apart means a dead JVM stops receiving traffic
in about 30 seconds.

### 6.2 Create the load balancer

Go to **EC2 → Load balancers → Create load balancer → Application Load Balancer**.

| Field | Value |
| --- | --- |
| Name | `ha-app-alb` |
| Scheme | Internet-facing |
| VPC | `ha-app-vpc` |
| Mappings | us-east-1a → `public-a`, us-east-1b → `public-b` |
| Security group | `sg-alb` (remove `default`) |
| Listener | HTTP `80` → forward to `ha-app-tg` |

Both AZs must be ticked — an ALB requires at least two subnets in different AZs, and
this is what gives the load balancing tier itself redundancy.

Copy the ALB's **DNS name** once it is active. It will not serve anything yet because
the target group is empty.

---

## Phase 7 — Auto Scaling Group

This is the phase that brings the application online. Go to **EC2 → Auto Scaling
groups → Create Auto Scaling group**.

1. **Name and template** — name `ha-app-asg`, launch template `ha-app-lt`.
2. **Network** — VPC `ha-app-vpc`, subnets **`app-a` and `app-b`**. Both, or you do
   not have a multi-AZ deployment.
3. **Load balancing** — choose **Attach to an existing load balancer**, then **Choose
   from your load balancer target groups**, and select `ha-app-tg`.
4. **Health checks** — turn on **Elastic Load Balancing health checks** and set the
   grace period to **300 seconds**.
5. **Group size** — desired `2`, minimum `2`, maximum `4`.
6. **Scaling policy** — **Target tracking**, metric **Average CPU utilization**,
   target `50`.

Two settings carry most of the weight here. Enabling **ELB health checks** is what
makes the group replace instances that are running but not *serving* — with the
default EC2-only health check, a crashed JVM on a healthy instance would sit in the
group forever, because EC2 sees nothing wrong with the virtual machine. The **300
second grace period** stops the group from killing instances that are still booting;
a Spring Boot app that needs 90 seconds to start would otherwise be terminated and
relaunched in an endless loop.

A minimum of two, spread over two subnets, is the core of the design: the ASG
balances instances across AZs automatically, so you always have one on each side.

> **Checkpoint.** Within about five minutes, `ha-app-tg` should show two targets in
> the **healthy** state, and `curl http://<alb-dns-name>/` should return your
> application's response. If targets are unhealthy, jump to
> [Troubleshooting](#troubleshooting).

---

## Phase 8 — HTTPS (recommended)

Skip this if you have no domain name; everything works over HTTP:80 for testing, but
do not run it that way in production, since credentials and session cookies would
cross the internet in plaintext.

Request a certificate in **ACM → Request certificate** for your domain, validate it
via DNS (ACM can create the Route 53 record for you), and wait for **Issued**. The
certificate must be in the same region as the ALB.

Then on the load balancer's **Listeners** tab, add a listener on **HTTPS 443**
forwarding to `ha-app-tg` with that certificate. Finally, edit the existing port 80
listener and change its action to **Redirect to URL** → HTTPS port 443 with status
**301**, so plain HTTP requests are upgraded rather than served.

In Route 53, create an **A record** for your domain with **Alias** enabled, pointing
at the load balancer. Use an alias record, not a CNAME — the ALB's IP addresses change
over time, and alias records track them automatically at no query cost.

---

## Phase 9 — Verify high availability

Building the architecture is not the same as proving it works. Run these three tests.

**Instance failure.** In **EC2 → Instances**, select one of the two application
instances and terminate it. Watch the target group: the remaining instance keeps
serving (`curl` in a loop should show no errors), the terminated target drains, and
within a few minutes the ASG launches a replacement in the same AZ that becomes
healthy on its own.

**Database failover.** In **RDS → Databases**, select `ha-app-db` and choose
**Actions → Reboot** with **Reboot with failover** ticked. This promotes the standby
and repoints the endpoint DNS. Expect writes to fail for 60–120 seconds, then recover
without any change on your side. If your application *stays* broken after the endpoint
recovers, its connection pool is holding dead connections — see Appendix A.

**Load distribution.** Run `for i in $(seq 20); do curl -s http://<alb-dns>/; done`
and confirm from the instance logs that both instances received traffic.

Worth adding once verified: CloudWatch alarms on the target group's
`UnHealthyHostCount` (greater than 0), the ALB's `HTTPCode_ELB_5XX_Count`, and RDS
`FreeStorageSpace` and `CPUUtilization`, all wired to an SNS topic with your email.
Without alarms you will find out about a failed AZ from a user rather than from AWS.

---

## Phase 10 — Teardown

Delete in this order. Dependencies block deletion, so out-of-order attempts fail with
errors that are not always clear about the cause.

1. **Auto Scaling group** `ha-app-asg` — this terminates the instances for you. Do not
   terminate them manually first; the ASG would just replace them.
2. **Load balancer** `ha-app-alb`, then **target group** `ha-app-tg`.
3. **RDS** `ha-app-db` — first use **Modify** to turn off deletion protection if you
   enabled it, then delete, declining the final snapshot for a test build.
4. **NAT Gateways**, then **Elastic IPs** under **Network & Security → Elastic IPs**.
   Releasing the EIPs is the step people forget, and an unattached Elastic IP is
   billed hourly.
5. **Internet Gateway** — detach from the VPC, then delete.
6. **VPC** — deleting it removes the subnets, route tables, and security groups.
7. **S3 bucket** (empty it first) and the **Secrets Manager secret**.

Check **Billing → Cost Explorer** the next day to confirm nothing is still running.

---

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Targets stuck **unhealthy** | App not listening on 8080, `/actuator/health` not exposed, or `sg-app` missing the rule from `sg-alb` |
| Targets never register | ASG not attached to the target group, or built in `public-*` subnets |
| ALB returns **502** | App is up but returning errors — check it starts successfully |
| ALB returns **504** | Security group path blocked, or the app is timing out on database calls |
| App can't reach the database | `sg-db` source is not `sg-app`, wrong endpoint, or DNS hostnames off (Phase 1.1) |
| Instances loop: launch, terminate, repeat | ELB health check grace period too short for app startup |
| User data seems not to run | Check `/var/log/user-data.log` on the instance |

To inspect an instance, use **EC2 → Connect → Session Manager** — no SSH key needed.
The three commands that answer most questions:

```bash
sudo systemctl status app.service     # is the service running?
sudo journalctl -u app.service -n 50  # why did it fail?
curl -v localhost:8080/actuator/health
```

If Session Manager does not offer a connection, the instance either lacks the
`AmazonSSMManagedInstanceCore` policy or has no outbound route — check its NAT
Gateway path from Phase 1.

---

## Appendix A — What the Spring Boot app must do

Three things are required for the app to work in this architecture.

**Expose a health endpoint.** Add the Actuator dependency and make sure
`/actuator/health` returns 200:

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
```

**Read configuration from the environment.** The user data script supplies
`SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`, and
`SPRING_DATASOURCE_PASSWORD` as environment variables, which Spring Boot binds
automatically. Do not commit credentials to `application.properties`.

**Survive database failover.** Configure the connection pool to discard dead
connections rather than hand them out after a failover:

```properties
management.endpoints.web.exposure.include=health,info
management.endpoint.health.probes.enabled=true
server.shutdown=graceful

spring.datasource.hikari.maximum-pool-size=10
spring.datasource.hikari.connection-timeout=3000
spring.datasource.hikari.validation-timeout=2000
spring.datasource.hikari.keepalive-time=30000
spring.datasource.hikari.max-lifetime=600000
```

Finally, the application must be **stateless** — no session data or uploads on local
disk. Any instance can be terminated at any moment, so session state belongs in the
database or ElastiCache, and uploads belong in S3.

---

## Appendix B — Moving this to infrastructure as code

Roughly 40 console steps produced this stack, and none of them are reproducible or
reviewable. Once you have built it by hand and understand the dependencies, the
natural next step is to express it as Terraform or CloudFormation so environments can
be recreated, diffed, and destroyed on demand.

Ask and I will generate the Terraform equivalent of this guide.
