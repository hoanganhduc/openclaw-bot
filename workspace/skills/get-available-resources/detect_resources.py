#!/usr/bin/env python3
from __future__ import annotations

import datetime
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil


def get_cpu_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "max_frequency_mhz": None,
        "architecture": platform.machine(),
        "processor": platform.processor(),
    }
    try:
        freq = psutil.cpu_freq()
        if freq:
            info["max_frequency_mhz"] = freq.max
            info["current_frequency_mhz"] = freq.current
    except Exception:
        pass
    return info


def get_memory_info() -> Dict[str, Any]:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_gb": round(mem.total / (1024**3), 2),
        "available_gb": round(mem.available / (1024**3), 2),
        "used_gb": round(mem.used / (1024**3), 2),
        "percent_used": mem.percent,
        "swap_total_gb": round(swap.total / (1024**3), 2),
        "swap_available_gb": round((swap.total - swap.used) / (1024**3), 2),
    }


def get_disk_info(path: Optional[str] = None) -> Dict[str, Any]:
    probe_path = path or os.getcwd()
    try:
        disk = psutil.disk_usage(probe_path)
        return {
            "path": probe_path,
            "total_gb": round(disk.total / (1024**3), 2),
            "available_gb": round(disk.free / (1024**3), 2),
            "used_gb": round(disk.used / (1024**3), 2),
            "percent_used": disk.percent,
        }
    except Exception as exc:
        return {"path": probe_path, "error": str(exc)}


def detect_nvidia_gpus() -> List[Dict[str, Any]]:
    gpus: List[Dict[str, Any]] = []
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 6:
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "memory_total_mb": float(parts[2]),
                            "memory_free_mb": float(parts[3]),
                            "driver_version": parts[4],
                            "compute_capability": parts[5],
                            "type": "NVIDIA",
                            "backend": "CUDA",
                        }
                    )
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    return gpus


def detect_amd_gpus() -> List[Dict[str, Any]]:
    gpus: List[Dict[str, Any]] = []
    try:
        result = subprocess.run(
            ["rocm-smi", "--showid", "--showmeminfo", "vram"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            gpu_index = 0
            for line in result.stdout.strip().splitlines():
                if "GPU" in line and "DID" in line:
                    gpus.append(
                        {
                            "index": gpu_index,
                            "name": "AMD GPU",
                            "type": "AMD",
                            "backend": "ROCm",
                            "info": line.strip(),
                        }
                    )
                    gpu_index += 1
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass
    return gpus


def detect_apple_silicon_gpu() -> Optional[Dict[str, Any]]:
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        cpu_brand = result.stdout.strip()
        if "Apple" in cpu_brand and any(chip in cpu_brand for chip in ["M1", "M2", "M3", "M4"]):
            gpu_info: Dict[str, Any] = {
                "name": cpu_brand,
                "type": "Apple Silicon",
                "backend": "Metal",
                "unified_memory": True,
            }
            try:
                profiler = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                for line in profiler.stdout.splitlines():
                    if "Chipset Model" in line:
                        gpu_info["chipset"] = line.split(":", 1)[1].strip()
                    elif "Total Number of Cores" in line:
                        gpu_info["gpu_cores"] = line.split(":", 1)[1].strip()
            except Exception:
                pass
            return gpu_info
    except Exception:
        pass
    return None


def get_gpu_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "nvidia_gpus": detect_nvidia_gpus(),
        "amd_gpus": detect_amd_gpus(),
        "apple_silicon": detect_apple_silicon_gpu(),
        "total_gpus": 0,
        "available_backends": [],
    }
    if info["nvidia_gpus"]:
        info["total_gpus"] += len(info["nvidia_gpus"])
        info["available_backends"].append("CUDA")
    if info["amd_gpus"]:
        info["total_gpus"] += len(info["amd_gpus"])
        info["available_backends"].append("ROCm")
    if info["apple_silicon"]:
        info["total_gpus"] += 1
        info["available_backends"].append("Metal")
    return info


def get_os_info() -> Dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def generate_recommendations(resources: Dict[str, Any]) -> Dict[str, Any]:
    recommendations: Dict[str, Any] = {
        "parallel_processing": {},
        "memory_strategy": {},
        "gpu_acceleration": {},
        "large_data_handling": {},
    }

    cpu_cores = resources["cpu"]["logical_cores"] or 1
    if cpu_cores >= 8:
        recommendations["parallel_processing"] = {
            "strategy": "high_parallelism",
            "suggested_workers": max(cpu_cores - 2, 1),
            "libraries": ["joblib", "multiprocessing", "dask"],
        }
    elif cpu_cores >= 4:
        recommendations["parallel_processing"] = {
            "strategy": "moderate_parallelism",
            "suggested_workers": max(cpu_cores - 1, 1),
            "libraries": ["joblib", "multiprocessing"],
        }
    else:
        recommendations["parallel_processing"] = {
            "strategy": "sequential",
            "note": "Limited cores detected; prefer sequential or lightly parallel execution.",
        }

    available_memory_gb = resources["memory"]["available_gb"]
    if available_memory_gb < 4:
        recommendations["memory_strategy"] = {
            "strategy": "memory_constrained",
            "libraries": ["zarr", "dask", "h5py"],
            "note": "Use chunked or out-of-core processing.",
        }
    elif available_memory_gb < 16:
        recommendations["memory_strategy"] = {
            "strategy": "moderate_memory",
            "libraries": ["dask", "zarr"],
            "note": "Chunk datasets larger than a few GB.",
        }
    else:
        recommendations["memory_strategy"] = {
            "strategy": "memory_abundant",
            "note": "Most single-machine workloads can fit in memory.",
        }

    gpu_info = resources["gpu"]
    if gpu_info["total_gpus"] > 0:
        recommendations["gpu_acceleration"] = {
            "available": True,
            "backends": gpu_info["available_backends"],
        }
        if "CUDA" in gpu_info["available_backends"]:
            recommendations["gpu_acceleration"]["suggested_libraries"] = [
                "pytorch",
                "tensorflow",
                "jax",
                "cupy",
                "rapids",
            ]
        elif "Metal" in gpu_info["available_backends"]:
            recommendations["gpu_acceleration"]["suggested_libraries"] = [
                "pytorch-mps",
                "tensorflow-metal",
                "jax-metal",
            ]
        elif "ROCm" in gpu_info["available_backends"]:
            recommendations["gpu_acceleration"]["suggested_libraries"] = [
                "pytorch-rocm",
                "tensorflow-rocm",
            ]
    else:
        recommendations["gpu_acceleration"] = {
            "available": False,
            "note": "No GPU detected; plan for CPU execution.",
        }

    disk_available_gb = resources["disk"].get("available_gb", 0)
    if disk_available_gb < 10:
        recommendations["large_data_handling"] = {
            "strategy": "disk_constrained",
            "note": "Use streaming, cleanup, or compression for intermediates.",
        }
    elif disk_available_gb < 100:
        recommendations["large_data_handling"] = {
            "strategy": "moderate_disk",
            "libraries": ["zarr", "h5py", "parquet"],
        }
    else:
        recommendations["large_data_handling"] = {
            "strategy": "disk_abundant",
            "note": "Sufficient space for larger intermediates.",
        }

    return recommendations


def detect_all_resources(output_path: Optional[str] = None) -> Dict[str, Any]:
    output = output_path or os.path.join(os.getcwd(), ".openclaw_resources.json")
    resources = {
        "timestamp": datetime.datetime.now().isoformat(),
        "os": get_os_info(),
        "cpu": get_cpu_info(),
        "memory": get_memory_info(),
        "disk": get_disk_info(),
        "gpu": get_gpu_info(),
    }
    resources["recommendations"] = generate_recommendations(resources)
    Path(output).write_text(json.dumps(resources, indent=2), encoding="utf-8")
    return resources


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Detect system resources for OpenClaw planning")
    parser.add_argument(
        "-o",
        "--output",
        default=".openclaw_resources.json",
        help="Output JSON file path (default: .openclaw_resources.json)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print the full JSON payload to stdout",
    )
    args = parser.parse_args()

    print("Detecting system resources...")
    resources = detect_all_resources(args.output)
    print(f"Resources detected and saved to: {args.output}")

    if args.verbose:
        print(json.dumps(resources, indent=2))

    print("Resource summary:")
    print(f"  OS: {resources['os']['system']} {resources['os']['release']}")
    print(f"  CPU: {resources['cpu']['logical_cores']} logical cores")
    print(
        f"  Memory: {resources['memory']['total_gb']} GB total, "
        f"{resources['memory']['available_gb']} GB available"
    )
    print(
        f"  Disk: {resources['disk'].get('total_gb', 'n/a')} GB total, "
        f"{resources['disk'].get('available_gb', 'n/a')} GB available"
    )
    if resources["gpu"]["total_gpus"] > 0:
        print(f"  GPU: {resources['gpu']['total_gpus']} detected ({', '.join(resources['gpu']['available_backends'])})")
    else:
        print("  GPU: none detected")


if __name__ == "__main__":
    main()
