# Alp 工具箱 v0.1.14

**修复 v0.1.13 的崩溃**：启动时日志出现 `Exception in thread 0x24-keepalive:
AttributeError: 'bool' object has no attribute 'is_set'` —— 快速保活线程一启动就退出了。

## 🐞 修复

- **保活线程崩溃（v0.1.13 实机回归）**：`DeviceWorker._stop` 是 **bool 标志**，而 v0.1.13
  的保活线程按 `threading.Event` 编写（调用了 `.is_set()` / `.wait()`）⇒ 线程启动即抛异常
  ⇒ **0.4 秒保活从未运行，目标转速修复实际未生效**。

  已改为与主循环一致的 bool 标志 + `time.sleep`。用**真实 `DeviceWorker(Config.load())`**
  验证：`_stop` 类型为 bool ✓、1.35 秒内重发 4 次 ✓、`stop()` 后线程正常退出 ✓、无异常 ✓。

- **目标转速被设备夺回（v0.1.13 的主体修复，本次真正生效）**：协议要求 ~0.4 秒周期重发
  固定转速，否则设备约 1 秒内会用自己的档位曲线夺回控制权。现在保活真正跑起来了。

## 校验和 (SHA256)

- `Alp工具箱.exe` — SHA256: `56561f116bf63aaac69074f9ab4983a96049b18db0b253eccb7ce84f31dcc783`
- `Alp工具箱-setup-v0.1.14.exe` — SHA256: `2ec9ad0bad745b9c861e4283bbbd09da338d34c677829e2b39630affe5fb4f33`
