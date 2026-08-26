"""Render the high-availability architecture diagram to PNG and SVG.

Usage:  python docs/generate_diagram.py
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

NAVY = "#232F3E"
ORANGE = "#FF9900"
PURPLE = "#8C4FFF"
GREEN = "#7AA116"
TEAL = "#00A4A6"
EC2 = "#ED7100"
RDS = "#3B48CC"
ALB = "#8C4FFF"
MUTED = "#5A6B86"

OUT_DIR = Path(__file__).resolve().parent


def box(ax, x0, y0, x1, y1, *, face, edge, lw=1.6, ls="-", radius=1.2, z=1):
    ax.add_patch(
        FancyBboxPatch(
            (x0, y0),
            x1 - x0,
            y1 - y0,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            facecolor=face,
            edgecolor=edge,
            linewidth=lw,
            linestyle=ls,
            mutation_aspect=1,
            zorder=z,
        )
    )


def container_label(ax, x, y, text, color, size=10):
    ax.text(x, y, text, ha="left", va="center", fontsize=size,
            color=color, fontweight="bold", zorder=5)


def node(ax, x0, y0, x1, y1, icon, title, subtitle, color):
    """A service card: colored icon tile on the left, two lines of text."""
    box(ax, x0, y0, x1, y1, face="#FFFFFF", edge=color, lw=1.8, radius=1.0, z=4)
    cy = (y0 + y1) / 2
    tile = 5.6
    box(ax, x0 + 2.4, cy - tile / 2, x0 + 2.4 + tile, cy + tile / 2,
        face=color, edge=color, radius=0.8, z=5)
    ax.text(x0 + 2.4 + tile / 2, cy, icon, ha="center", va="center",
            fontsize=8, color="#FFFFFF", fontweight="bold", zorder=6)
    tx = x0 + 2.4 + tile + 2.6
    ax.text(tx, cy + 1.7, title, ha="left", va="center", fontsize=11.5,
            color=NAVY, fontweight="bold", zorder=6)
    ax.text(tx, cy - 1.9, subtitle, ha="left", va="center", fontsize=8.8,
            color=MUTED, zorder=6)


def arrow(ax, p0, p1, *, color=NAVY, lw=2.0, ls="-", z=7):
    ax.annotate(
        "",
        xy=p1,
        xytext=p0,
        arrowprops=dict(arrowstyle="-|>", color=color, linewidth=lw,
                        linestyle=ls, shrinkA=0, shrinkB=0, mutation_scale=20),
        zorder=z,
    )


def caption(ax, x, y, text, *, color=MUTED, size=8.6, ha="center", style="italic"):
    ax.text(x, y, text, ha=ha, va="center", fontsize=size, color=color,
            style=style, zorder=8,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#FFFFFF",
                      edgecolor="none", alpha=0.92))


def build():
    fig, ax = plt.subplots(figsize=(14, 11))
    ax.set_xlim(0, 140)
    ax.set_ylim(0, 110)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("#FFFFFF")

    ax.text(70, 106.5, "Highly Available Spring Boot Web Application on AWS",
            ha="center", va="center", fontsize=19, color=NAVY, fontweight="bold")
    ax.text(70, 102.8, "Multi-AZ - Auto Scaling - Managed MySQL with automatic failover",
            ha="center", va="center", fontsize=10.5, color=MUTED)

    # Public internet
    node(ax, 52, 93, 88, 99.5, "WWW", "Internet", "End users / clients", NAVY)

    # AWS account boundary
    box(ax, 3, 2, 137, 88, face="#F7F9FB", edge=NAVY, lw=2.0, radius=1.8, z=0)
    container_label(ax, 6, 85.4, "AWS Cloud   |   Region: us-east-1", NAVY, size=11)

    # VPC
    box(ax, 7, 5, 133, 79, face="#FAF7FF", edge=PURPLE, lw=1.8, radius=1.5, z=1)
    container_label(ax, 10, 76.4, "VPC  10.0.0.0/16", PURPLE)

    # Internet Gateway sits on the VPC boundary
    node(ax, 52, 75.6, 88, 82.2, "IGW", "Internet Gateway", "Ingress / egress edge", PURPLE)

    az = [
        (11, 69, "Availability Zone A  (us-east-1a)", "10.0.1.0/24", "10.0.11.0/24", "10.0.21.0/24"),
        (71, 129, "Availability Zone B  (us-east-1b)", "10.0.2.0/24", "10.0.12.0/24", "10.0.22.0/24"),
    ]

    for x0, x1, az_name, pub_cidr, app_cidr, db_cidr in az:
        box(ax, x0, 8, x1, 70, face="#FFFFFF", edge=MUTED, lw=1.4, ls=(0, (5, 4)), radius=1.2, z=2)
        container_label(ax, x0 + 2.5, 67.5, az_name, MUTED, size=9.5)

        sx0, sx1 = x0 + 3, x1 - 3
        box(ax, sx0, 52, sx1, 65, face="#F3F8E8", edge=GREEN, lw=1.4, radius=1.0, z=3)
        container_label(ax, sx0 + 2, 63.2, f"Public Subnet  {pub_cidr}", GREEN, size=9)

        box(ax, sx0, 33, sx1, 49, face="#EAF6F6", edge=TEAL, lw=1.4, radius=1.0, z=3)
        container_label(ax, sx0 + 2, 47.2, f"Private App Subnet  {app_cidr}", TEAL, size=9)

        box(ax, sx0, 10, sx1, 26, face="#EAF6F6", edge=TEAL, lw=1.4, radius=1.0, z=3)
        container_label(ax, sx0 + 2, 24.2, f"Private DB Subnet  {db_cidr}", TEAL, size=9)

    # Load balancer spans both public subnets
    node(ax, 24, 53.5, 116, 60.5, "ALB", "Application Load Balancer",
         "Internet-facing - HTTPS :443 - health checks on /actuator/health", ALB)

    # Application tier
    node(ax, 22, 34.5, 58, 45, "EC2", "EC2 - Spring Boot", "Auto Scaling Group - port 8080", EC2)
    node(ax, 82, 34.5, 118, 45, "EC2", "EC2 - Spring Boot", "Auto Scaling Group - port 8080", EC2)

    # Database endpoint + Multi-AZ pair
    node(ax, 47, 27.3, 93, 32.7, "DNS", "RDS Endpoint",
         "JDBC :3306 - always resolves to the primary", MUTED)
    node(ax, 22, 11.5, 58, 22, "RDS", "RDS MySQL - Primary", "writer - Multi-AZ", RDS)
    node(ax, 82, 11.5, 118, 22, "RDS", "RDS MySQL - Standby", "passive - promoted on failover", RDS)

    # Traffic flow
    arrow(ax, (70, 93), (70, 82.2))
    arrow(ax, (70, 75.6), (70, 60.5))
    arrow(ax, (40, 53.5), (40, 45))
    arrow(ax, (100, 53.5), (100, 45))
    arrow(ax, (40, 34.5), (58, 32.7))
    arrow(ax, (100, 34.5), (82, 32.7))
    arrow(ax, (60, 27.3), (42, 22))
    arrow(ax, (80, 27.3), (98, 22), color=MUTED, lw=1.6, ls=(0, (4, 3)))

    # Synchronous replication between the RDS pair
    arrow(ax, (58, 15), (82, 15), color=RDS, lw=1.6, ls=(0, (4, 3)))
    arrow(ax, (82, 18.5), (58, 18.5), color=RDS, lw=1.6, ls=(0, (4, 3)))
    caption(ax, 70, 16.8, "synchronous replication", color=RDS)

    caption(ax, 70, 88.6, "HTTPS")
    caption(ax, 73.5, 72.6, "forwards only to healthy targets", ha="left")

    fig.tight_layout(pad=0.4)
    for ext, dpi in (("png", 130), ("svg", 130)):
        path = OUT_DIR / f"architecture.{ext}"
        fig.savefig(path, dpi=dpi, facecolor="#FFFFFF", bbox_inches="tight")
        print(f"wrote {path}")
    plt.close(fig)


if __name__ == "__main__":
    build()
