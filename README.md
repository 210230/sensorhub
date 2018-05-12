# Sensor Hub 系统使用说明 V1.0
最后编辑日期 2018/05/12

## 更新软件
最新代码使用 <pre>git clone https://github.com/210230/sensorhub.git</pre>来下载，建议初次使用前删除 <pre>/home/dst</pre> 目录下的sensorhub目录，并采用上述命令重新下载一次。以后安装时可以在sensorhub目录下执行<pre>git pull</pre>来进行更新。

## 配置设备（更新系统配置表）
传感器映射在系统中的位置由用户配置确定，软件提供三种配置方式：
1. 固定在代码中的默认配置，当无其他配置时，系统使用该配置。
<p>
    #reg, description,      bus, node, addr, size
    [0,   'wind speed',     1,   1,    0,    2],
    [2,   'wind direction', 1,   2,    0,    2],
    [4,   'temp and herm',  1,   3,    0,    4],
    [8,   'window',         1,   4,    0,    6],
</p>
其中，reg为系统映射寄存器地址，bus为485总线，可选值为1或者2，分别对应两路485端口，node为设备id（也称设备地址），该地址在本系统的485总线上必须和传感器一一对应且唯一，addr是传感器上485总线的地址，size是传感器上485寄存器的大小。
该定义适用于本系统的所有三种方法。
2. sensorhub目录下的dstcommon.conf文件，以及
3. sensorhub目录下的dstxxxxxxxxxxxx.conf文件（x为16进制数字，12个x组成cpuid）
当dstxxxxxxxxxxxx.conf存在时具有最高优先级，但仅仅对应唯一cpuid的设备，dstcommon.conf优先级比代码中的默认配置高，可以适用于所有具有合法cpuid的设备。

## 配置传感器
本系统默认采用华控兴业的485接口传感器，波特率固定为9600bps。由于传感器的默认配置不确定，所以在使用前需要在电脑端根据系统配置表进行id（设备地址）的配置，否则系统将无法正确的识别传感器的类型以及对应的寄存器位置。

配置方法详见各种传感器的用户手册，比如风速传感器，采用如下的指令将id变为3：
<pre>02 06 20 00 00 03 C2 38</pre>
以上字节均为16进制，最后两个字节为校验位，可以参考下面的网址进行在线计算
<pre>http://cht.nahua.com.tw/index.php?url=http://cht.nahua.com.tw/software/crc16/&key=Modbus,%20RTU,%20CRC16&title=%E8%A8%88%E7%AE%97%20Modbus%20RTU%20CRC16</pre>
打开任意串口工具，连好传感器的485接口，接好电源（可以借用系统的485供电），将上述指令写入传感器，传感器应可以从新的地址读取数据（通过03命令）

若系统采集出现问题，也建议先用电脑连接串口线确认传感器配置无误。

## 系统自动更新
TBD

## 系统自启动管理
TBD
