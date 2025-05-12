#!/usr/bin/env python3

import click
import socket
import aiohttp
import asyncio
import subprocess
import logging
from typing import Dict, Tuple
from datetime import datetime

# python3.6.8
# pip3 install --upgrade pip
# pip3 install multidict==5.2.0
# pip3 install aiohttp==3.8.6 click==8.0.4
# 钉钉相关库不再需要
# pip3 install dingtalkchatbot==1.5.7 

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def get_node_info() -> Dict[str, str]:
    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
        return {
            "name": hostname,
            "ip": ip_address
        }
    except Exception as e:
        logger.error(f"Failed to get node info: {str(e)}")
        return {
            "name": "unknown",
            "ip": "unknown"
        }

async def send_teams_alert(webhook_url: str, content: Dict, mention_all: bool = False) -> None:
    """Send alert to Microsoft Teams"""
    try:
        if not webhook_url:
            logger.warning("Teams webhook URL not provided, skipping alert")
            return

        async with aiohttp.ClientSession() as session:
            # Format message
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status = "[HEALTHY]" if content["is_healthy"] else "[UNHEALTHY]"
            
            # 获取节点信息
            node_info = get_node_info()
            
            msg_parts = []
            
            # 添加@所有人的格式
            if mention_all:
                msg_parts.append("<at>everyone</at>\n")
            
            msg_parts.extend([
                f"ETH FullNode Health Check Report - {timestamp}",
                f"\nNode: {node_info['name']}",
                f"\nIP: {node_info['ip']}",
                f"\nStatus: {status}",
                "\n\nDetails:"
            ])

            # Block sync status
            block_check = content["checks"]["block_sync"]
            if "error" in block_check["details"]:
                msg_parts.append(f"* Block Sync: [FAIL] Error - {block_check['details']['error']}")
            else:
                diff = block_check["details"]["block_difference"]
                sync_status = "[OK]" if block_check["status"] == "ok" else "[FAIL]"
                msg_parts.append(f"* Block Sync: {sync_status} Difference: {diff} blocks")

            # Disk space status
            disk_check = content["checks"]["disk_space"]
            if "error" in disk_check["details"]:
                msg_parts.append(f"* Disk Space: [FAIL] Error - {disk_check['details']['error']}")
            else:
                space = disk_check["details"]["available_space_gb"]
                disk_status = "[OK]" if disk_check["status"] == "ok" else "[FAIL]"
                msg_parts.append(f"* Disk Space: {disk_status} Available: {space}G")

            # Container status
            container_check = content["checks"]["containers"]
            if "error" in container_check["details"]:
                msg_parts.append(f"* Containers: [FAIL] Error - {container_check['details']['error']}")
            else:
                container_status = "[OK]" if container_check["status"] == "ok" else "[FAIL]"
                if container_check["status"] == "ok":
                    msg_parts.append(f"* Containers: {container_status} All running")
                else:
                    missing = ", ".join(container_check["details"]["missing_containers"])
                    msg_parts.append(f"* Containers: {container_status} Missing: {missing}")

            final_msg = "\n".join(msg_parts)
            
            # Teams 格式
            teams_payload = {
                "text": final_msg
            }
            
            # 发送到 Teams webhook
            async with session.post(webhook_url, json=teams_payload) as response:
                if response.status != 200:
                    response_text = await response.text()
                    logger.error(f"Teams webhook returned error: {response.status}, {response_text}")
                    return
                    
            logger.info("Teams alert sent successfully")
        
    except Exception as e:
        logger.error(f"Failed to send Teams alert: {str(e)}")

async def get_block_number(url: str) -> int:
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            if response.status != 200:
                raise Exception(f"Failed to get block number from {url}")
            data = await response.json()
            return int(data["result"], 16)

async def check_block_sync() -> Tuple[bool, Dict]:
    try:
        chainstack_url = "https://ethereum-mainnet.core.chainstack.com/09e453b884cef2b4983f653148231787"
        local_url = "http://localhost:8545"
        
        local_block = await get_block_number(local_url)
        chainstack_block = await get_block_number(chainstack_url)
        block_diff = abs(local_block - chainstack_block)
        
        is_synced = block_diff <= 20
        return is_synced, {
            "block_difference": block_diff,
            "local_block": local_block,
            "chainstack_block": chainstack_block
        }
    except Exception as e:
        logger.error(f"Block sync check failed: {e}")
        return False, {"error": str(e)}

def check_disk_space(root_path: str) -> Tuple[bool, Dict]:
    try:
        df_output = subprocess.check_output(["df", "-h"]).decode()
        for line in df_output.split('\n'):
            if root_path in line:
                parts = line.split()
                available = parts[3]
                size = float(available[:-1])  
                unit = available[-1].upper()  
                
                if unit == 'T':
                    available_gb = size * 1024
                elif unit == 'M':
                    available_gb = size / 1024
                else: 
                    available_gb = size
                    
                is_sufficient = available_gb >= 100
                return is_sufficient, {
                    "available_space_gb": round(available_gb, 2),
                    "required_min_gb": 100
                }
        return False, {"error": f"Root path {root_path} not found"}
    except Exception as e:
        logger.error(f"Disk space check failed: {e}")
        return False, {"error": str(e)}

def check_containers() -> Tuple[bool, Dict]:
    try:
        docker_ps = subprocess.check_output(["docker", "ps"]).decode()
        required_containers = ["mainnet-prysm-1", "mainnet-geth-1"]
        running_containers = []
        missing_containers = []
        
        for container in required_containers:
            if container in docker_ps:
                running_containers.append(container)
            else:
                missing_containers.append(container)
                
        return len(missing_containers) == 0, {
            "running_containers": running_containers,
            "missing_containers": missing_containers
        }
    except Exception as e:
        logger.error(f"Container check failed: {e}")
        return False, {"error": str(e)}

def run_async(coro):
    """Compatible async runner for Python 3.6"""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(coro)

@click.command()
@click.option('--root-path', default='/dev/mapper/centos-root', 
              help='Disk root path, default is /dev/mapper/centos-root')
@click.option('--json-output', is_flag=True, 
              help='Output results in JSON format')
@click.option('--teams-webhook', default='',
              help='Microsoft Teams webhook URL for alerts')
@click.option('--mention-all', is_flag=True, 
              help='Mention everyone in Teams alert')
def check_health(root_path: str, json_output: bool, teams_webhook: str, mention_all: bool = False):
    """Ethereum node health check tool"""
    import json
    
    block_sync_ok, block_status = run_async(check_block_sync())
    disk_ok, disk_status = check_disk_space(root_path)
    containers_ok, container_status = check_containers()
    
    result = {
        "is_healthy": all([block_sync_ok, disk_ok, containers_ok]),
        "checks": {
            "block_sync": {
                "status": "ok" if block_sync_ok else "error",
                "details": block_status
            },
            "disk_space": {
                "status": "ok" if disk_ok else "error",
                "details": disk_status
            },
            "containers": {
                "status": "ok" if containers_ok else "error",
                "details": container_status
            }
        }
    }
    
    # Send Teams alert
    if teams_webhook:
        run_async(send_teams_alert(teams_webhook, result, mention_all))
    
    if json_output:
        click.echo(json.dumps(result, indent=2))
        return
    
    click.echo("\nETH FullNode Health Check Report:")
    click.echo("=" * 50)
    
    click.echo("\n1. Block Sync Status:")
    if "error" in block_status:
        click.echo(f"[FAIL] Check failed: {block_status['error']}")
    else:
        status = "[OK]" if block_sync_ok else "[FAIL]"
        click.echo(f"{status} (Difference: {block_status['block_difference']} blocks)")
        click.echo(f"   Local block: {block_status['local_block']}")
        click.echo(f"   Remote block: {block_status['chainstack_block']}")
    
    click.echo("\n2. Disk Space Status:")
    if "error" in disk_status:
        click.echo(f"[FAIL] Check failed: {disk_status['error']}")
    else:
        status = "[OK]" if disk_ok else "[FAIL]"
        click.echo(f"{status} (Available: {disk_status['available_space_gb']}G, Required: >=100G)")
    
    click.echo("\n3. Container Status:")
    if "error" in container_status:
        click.echo(f"[FAIL] Check failed: {container_status['error']}")
    else:
        status = "[OK]" if containers_ok else "[FAIL]"
        if containers_ok:
            click.echo(f"{status} (All required containers are running)")
        else:
            click.echo(f"{status} (Missing containers: {', '.join(container_status['missing_containers'])})")
    
    click.echo("\nOverall Status:")
    status = "[OK]" if result["is_healthy"] else "[FAIL]"
    click.echo(f"{status}")
    click.echo("=" * 50 + "\n")

if __name__ == '__main__':
    check_health()