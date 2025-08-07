# evm_information.py 负责从节点上获取所有必要信息；
# 这些信息包括交易的标准化trace、涉及的contract address以及对应的bytecode；
# 包含对trace的结构定义；
# 包含获取每个step对应的contract address的逻辑；
# 不涉及其他对bytecode和trace的分析逻辑。

from typing import List, Dict, TypedDict, Set # 标准化数据结构定义
import logging # 标准化数据结构定义
import json
from web3 import Web3 # 导入Web3库用于与以太坊节点交互

logging.basicConfig(level=logging.INFO) # 设置日志级别为INFO
logger = logging.getLogger(__name__) # 创建日志记录器

# 标准化数据结构定义
class StandardizedStep(TypedDict): # 定义一个字典类型，包含以下字段
    address: str  # 0x开头的十六进制字符串
    pc: str       # 0x开头的十六进制字符串
    opcode: str   # 操作码名称
    stack: List[str]  # 0x开头的十六进制字符串

class StandardizedTrace(TypedDict): # 定义一个字典类型，包含以下字段
    tx_hash: str               # 0x开头的十六进制交易哈希
    steps: List[StandardizedStep]

class ContractBytecode(TypedDict):
    address: str  # 0x开头的十六进制字符串
    bytecode: str  # 0x开头的十六进制字符串

class TraceFormatter:
    def __init__(self, provider_url: str): # 初始化函数，接收一个以太坊节点的URL
        self.web3 = Web3(Web3.HTTPProvider(provider_url)) # 创建Web3实例
        if not self.web3.is_connected(): # 检查是否连接成功
            raise ConnectionError("无法连接到以太坊节点，请检查provider URL是否正确")

    # 地址标准化
    def _normalize_address(self, address: str) -> str: # 定义一个函数，接收一个地址字符串，返回一个标准化的地址字符串
        if not address:
            return ""
        try:
            checksum_addr = Web3.to_checksum_address(address) # 使用Web3库将地址转换为校验和格式
            return checksum_addr.lower() # 返回小写格式的校验和地址
        except:
            return ""  # 无效地址返回空

    # PC标准化
    def _normalize_pc(self, pc: int) -> str:
        return self.web3.to_hex(pc) # 使用Web3库将整数PC转换为十六进制字符串

    # 栈数据标准化
    def _normalize_stack(self, raw_stack: List[str]) -> List[str]:
        normalized = []
        for item in raw_stack or []: # 遍历原始栈数据
            if not item:
                # 空元素保留0x前缀的空值表示（而非64个0）
                normalized.append("0x")
                continue
            str_item = str(item) 
            # 确保0x前缀，不处理长度
            if str_item.startswith("0x"):
                normalized.append(str_item)
            else:
                normalized.append(f"0x{str_item}")
        return normalized

    # 获取交易初始目标地址
    def _get_initial_address(self, tx_hash: str) -> str:
        tx = self.web3.eth.get_transaction(tx_hash) # 使用Web3库获取指定交易哈希的交易信息
        return tx.get("to", "") # 获取交易的目标地址（合约或外部账户）

    # 获取并标准化trace,计算contract address
    def get_standardized_trace(self, tx_hash: str) -> StandardizedTrace:
        trace_config = {
            "enableMemory": False,
            "disableStack": False,
            "disableStorage": False,
            "enableReturnData": False
        } # 配置trace选项，禁用内存、栈和存储的跟踪，启用返回数据跟踪
        
        try:
            raw_trace = self.web3.manager.request_blocking(
                "debug_traceTransaction", 
                [tx_hash, trace_config] # 使用Web3库的debug_traceTransaction方法获取交易的trace信息
            ) # 使用Web3库的debug_traceTransaction方法获取交易的trace信息
            struct_logs = raw_trace.get("structLogs", []) # 获取结构化日志信息
            
            steps = []
            # 初始地址（交易直接调用的合约）
            initial_address = self._normalize_address(self._get_initial_address(tx_hash))
            current_address = initial_address  # 当前步骤的地址
            next_address = initial_address     # 下一个步骤的地址（初始与当前相同）
            # 调用栈：记录合约调用层级
            call_stack = [initial_address] if initial_address else [] # 初始化调用栈，初始地址为交易直接调用的合约地址
            
            for step in struct_logs:
                pc = step.get("pc", 0) # 获取当前步骤的PC
                opcode = step.get("op", "").upper() # 获取当前步骤的opcode
                raw_stack = step.get("stack", []) # 获取当前步骤的栈信息
                
                # 1. 处理合约调用：下一个步骤切换到新地址
                if opcode in {"CALL", "CALLCODE", "DELEGATECALL", "STATICCALL"}:
                    if len(raw_stack) >= 2:  # 确保栈中有目标地址参数
                        to_address = self._normalize_address(raw_stack[-2])
                        if to_address:  
                            # 当前地址压入调用栈（记录调用者）
                            call_stack.append(current_address)
                            # 下一个步骤切换到新地址
                            next_address = to_address
                
                # 2. 处理合约创建：下一个步骤切换到新合约地址
                elif opcode in ["CREATE", "CREATE2"]:
                    new_address = ""  # 实际需从step结果提取新地址，此处简化
                    if new_address:
                        call_stack.append(current_address)
                        next_address = new_address  # 下一个步骤切换到新地址
                
                # 3. 处理终止指令：下一个步骤返回上一层地址
                elif opcode in {"STOP", "RETURN", "REVERT", "INVALID", "SELFDESTRUCT"} and len(call_stack) > 1:
                        next_address = call_stack[-1]  # 下一个步骤切换到上一层地址
                        call_stack.pop()  # 移除当前层
                
                # 4. 其他指令：下一个步骤地址不变
                else:
                    next_address = current_address  # 保持当前地址
                
                # 记录当前步骤的信息（使用current_address，未切换）
                steps.append({
                    "address": current_address,
                    "pc": self._normalize_pc(pc),
                    "opcode": opcode,
                    "stack": self._normalize_stack(raw_stack)
                })
                
                # 5. 更新当前地址为next_address（为下一个步骤做准备）
                current_address = next_address
            
            return {
                "tx_hash": tx_hash,
                "steps": steps
            }
            
        except Exception as e: 
            logger.error(f"处理trace失败: {e}") # 记录错误信息
            raise

    # 提取合约地址
    def extract_contracts_from_trace(self, standardized_trace: StandardizedTrace) -> Set[str]:
        return {step["address"] for step in standardized_trace["steps"] if step["address"]}

    # 获取单个合约字节码
    def get_contract_bytecode(self, contract_address: str) -> ContractBytecode:
        normalized_addr = self._normalize_address(contract_address)
        if not normalized_addr or not self.web3.is_address(normalized_addr):
            raise ValueError(f"无效地址（需0x开头的十六进制）: {contract_address}")

        try:
            bytecode = self.web3.eth.get_code(Web3.to_checksum_address(normalized_addr))
            return {
                "address": normalized_addr,
                "bytecode": self.web3.to_hex(bytecode)
            }
        except Exception as e:
            logger.error(f"获取合约字节码失败: {e}")
            raise

    # 获取所有涉及的合约字节码
    def get_all_contracts_bytecode(self, tx_hash: str) -> List[ContractBytecode]:
        trace = self.get_standardized_trace(tx_hash)
        contracts = self.extract_contracts_from_trace(trace)
        return [self.get_contract_bytecode(addr) for addr in contracts if addr]