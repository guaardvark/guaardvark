import torch
import psutil
import logging

logger = logging.getLogger(__name__)


class HardwareService:
    @staticmethod
    def get_system_capabilities():
        """
        Detects system hardware capabilities (GPU, RAM) and recommends training parameters.
        """
        caps = {
            "device_type": "cpu",
            "gpu_name": None,
            "vram_total_mb": 0,
            "ram_total_mb": 0,
            "recommended_config": {
                "batch_size": 1,
                "gradient_accumulation_steps": 4,
                "max_seq_length": 512,
                "use_4bit": False,
                "cpu_offload": False,
                "lora_rank": 8,
            },
        }

        # 1. System RAM
        try:
            mem = psutil.virtual_memory()
            caps["ram_total_mb"] = int(mem.total / (1024 * 1024))
        except Exception as e:
            logger.warning(f"Failed to detect system RAM: {e}")

        # 2. GPU Detection via PyTorch
        if torch.cuda.is_available():
            try:
                caps["device_type"] = "gpu"
                caps["gpu_name"] = torch.cuda.get_device_name(0)
                # Convert bytes to MB
                caps["vram_total_mb"] = int(
                    torch.cuda.get_device_properties(0).total_memory / (1024 * 1024)
                )

                # --- Intelligent Recommendations based on VRAM ---
                vram = caps["vram_total_mb"]

                # Default Config for GPU
                config = caps["recommended_config"]
                config["use_4bit"] = (
                    True  # Assume 4-bit is desired for efficiency on consumer cards
                )
                config["lora_rank"] = 16

                if vram >= 22000:  # 24GB+ (3090/4090)
                    config.update(
                        {
                            "batch_size": 4,
                            "gradient_accumulation_steps": 2,
                            "max_seq_length": 4096,
                            "cpu_offload": False,
                        }
                    )
                elif vram >= 15000:  # 16GB (4080/4070 Ti)
                    config.update(
                        {
                            "batch_size": 2,
                            "gradient_accumulation_steps": 4,
                            "max_seq_length": 2048,
                            "cpu_offload": False,
                        }
                    )
                elif vram >= 11000:  # 12GB (3060/4070)
                    config.update(
                        {
                            "batch_size": 1,
                            "gradient_accumulation_steps": 4,
                            "max_seq_length": 2048,
                            "cpu_offload": True,  # Safety net
                        }
                    )
                elif vram >= 7000:  # 8GB
                    config.update(
                        {
                            "batch_size": 1,
                            "gradient_accumulation_steps": 8,
                            "max_seq_length": 1024,
                            "cpu_offload": True,
                        }
                    )
                else:  # < 8GB
                    config.update(
                        {
                            "batch_size": 1,
                            "gradient_accumulation_steps": 8,
                            "max_seq_length": 512,
                            "cpu_offload": True,
                        }
                    )

            except Exception as e:
                logger.error(f"Error detecting GPU properties: {e}")

        return caps
