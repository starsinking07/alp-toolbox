# Alp 工具箱

黑鲨风神 Pro(BRB02)磁吸式半导体散热器的**第三方开源控制工具**,替代官方"黑鲨装备箱"。

Python + PySide6,支持 **USB / 蓝牙 BLE 双通道**(自动切换),通信协议全部实机逆向验证。
界面设计语言复刻 [FanControlPortable](https://github.com/Doongjohn/FanControlPortable)(MIT)。

**作者:苏晓沉(StarSinking)**

## 功能

- **双通道连接**:USB(libusb 中断传输)/ 蓝牙 BLE,拔线自动切蓝牙、插线自动切回
- **状态页**:CPU/GPU 半圆仪表、实时转速、温度/功耗统计
- **智能变频**:19 锚点可视化曲线编辑器,PC 端按温度实时下发转速
- **4 挡位 + 12 点滑条**手动控制,挡位灯联动(切挡自动换灯色)
- **智能启停** / **自适应学习** / **温升预判**
- **灯效面板**:彩色流动 / 彩色循环 / 呼吸 / 常亮 / 闪烁 / 响应 / 刷新,
  单色/彩色、色调、亮度、速度,与官方装备箱同款参数
- **灯光开关**、灯效配置设备侧持久化
- 温度 EMA 平滑 + 尖峰过滤、温度历史(30 分钟趋势)、托盘实时数据
- 全局快捷键(Ctrl+Alt+F1 循环挡位 · Ctrl+Alt+F2 智能变频)、亮/暗主题

## 下载 / 安装

### 免安装版(推荐)

从 Release 页下载 `Alp工具箱.exe`,双击运行(UAC 提权后可读取 CPU 温度)。

### 源码运行

```bash
git clone <本仓库>
cd alp-toolbox
pip install PySide6 pyusb libusb-package pythonnet bleak
python main.py
```

- 温度/功耗读取使用 `LHM/LibreHardwareMonitorLib.dll`(MPL-2.0,已随仓库附带),
  程序会自动在打包资源、`LHM/` 目录下定位。
- **CPU 温度**需要管理员权限 + [PawnIO](https://github.com/hirschmann/pawnio) 驱动;
  无提权时自动降级显示 GPU 温度。
- 打包:`pip install pyinstaller && pyinstaller --clean -y Alp.spec`

## 硬件协议

通信协议已完整逆向并整理成文档,**欢迎集成到其他项目**:

- [PROTOCOL.md](PROTOCOL.md) — 完整通信协议(传输层 / 帧格式 / 命令总表 / 制冷控制 / 心跳)
- [LIGHTING.md](LIGHTING.md) — RGB 灯效协议独立文档(8 种模式 / 参数字段 / 官方实帧 / 参考实现)

> ⚠️ **危险警告**:向设备发送 LEN 与实际不符、或含非法曲线锚点的帧,会被固件写入
> flash 导致开机循环崩溃。请勿在未确认字段含义时发送写命令。

## 系统要求

- Windows 10 / 11
- 黑鲨风神 Pro(BRB02)散热器(USB 连接,或长按按键 3–5 秒进入蓝牙配对)
- CPU 温度显示:管理员权限 + PawnIO 驱动(可选)

## 免责声明

本项目为非官方工具,与黑鲨 / 小米无关。涉及固件通信,存在理论上的设备风险,
请自行评估并谨慎使用。作者不对因使用本软件造成的任何损失负责。

## 致谢

- [FanControlPortable](https://github.com/Doongjohn/FanControlPortable)(MIT)— 界面设计语言与 Manrope 字体
- [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)(MPL-2.0)— CPU/GPU 温度与功耗读取
- 黑鲨官方"装备箱"— 协议逆向参考来源

## License

[MIT](LICENSE) © 2026 苏晓沉 (StarSinking)
