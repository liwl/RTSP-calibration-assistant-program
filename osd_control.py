"""
OSD 控制模块

本模块提供相机 OSD (On-Screen Display) 文字叠加的控制功能，
支持清除和恢复相机画面上的文字叠加（如时间、通道名等）。

支持的协议：
1. ONVIF 标准协议：通过 SOAP 调用 GetOSDs/SetOSD 接口
2. HTTP 私有协议：海康威视 ISAPI、大华 CGI

主要功能：
- 获取当前 OSD 配置
- 清除 OSD 文字叠加
- 恢复 OSD 原始配置
- 支持多品牌相机（海康威视、大华、宇视）

使用示例：
    controller = OSDController()
    # 清除 OSD
    ok = controller.disable_osd('海康威视', '192.168.1.64', '554', 'admin', '12345', '1')
    # 恢复 OSD
    ok = controller.enable_osd('海康威视', '192.168.1.64', '554', 'admin', '12345', '1')
"""

import base64
import hashlib
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from xml.etree import ElementTree as ET

# ONVIF 命名空间定义
ONVIF_NS = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope',
    'wsse': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd',
    'wsu': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd',
    'tds': 'http://www.onvif.org/ver10/device/wsdl',
    'tmd': 'http://www.onvif.org/ver10/deviceIO/wsdl',
    'tt': 'http://www.onvif.org/ver10/schema',
}


class OSDController:
    """
    OSD 控制器
    
    支持 ONVIF 标准协议和 HTTP 私有协议，
    可清除/恢复相机画面上的文字叠加。
    
    协议优先级：
    1. 先尝试 ONVIF 协议（通用性好）
    2. ONVIF 失败时回退到 HTTP 私有协议
    
    备份恢复机制：
    - 清除 OSD 前备份原始配置到 _onvif_backup
    - 恢复 OSD 时从备份还原
    """
    
    def __init__(self):
        """初始化 OSD 控制器"""
        self._disabled = False          # OSD 是否已禁用
        self._onvif_backup = {}         # ONVIF OSD 配置备份

    @property
    def is_disabled(self):
        """
        查询 OSD 是否已禁用
        
        Returns:
            bool: True 表示 OSD 已禁用
        """
        return self._disabled

    def disable_osd(self, brand, ip, port, user, pwd, channel):
        """
        禁用 OSD 文字叠加
        
        优先使用 ONVIF 协议，失败时回退到 HTTP 私有协议。
        
        Args:
            brand (str): 相机品牌（海康威视/大华/宇视）
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        # 先尝试 ONVIF 协议
        if self._onvif_disable(ip, port, user, pwd):
            self._disabled = True
            return True
        
        # ONVIF 失败，回退到 HTTP 私有协议
        ok = self._http_disable(brand, ip, port, user, pwd, channel)
        if ok:
            self._disabled = True
        return ok

    def enable_osd(self, brand, ip, port, user, pwd, channel):
        """
        恢复 OSD 文字叠加
        
        优先使用 ONVIF 备份恢复，失败时使用 HTTP 私有协议。
        
        Args:
            brand (str): 相机品牌
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        # 先尝试从 ONVIF 备份恢复
        if self._onvif_backup and self._onvif_enable(ip, port, user, pwd):
            self._disabled = False
            self._onvif_backup = {}
            return True
        
        # 备份恢复失败，使用 HTTP 私有协议
        ok = self._http_enable(brand, ip, port, user, pwd, channel)
        if ok:
            self._disabled = False
        return ok

    # ── ONVIF 协议实现 ──────────────────────────────────────

    def _wsse_digest(self, password, nonce_b64, created):
        """
        计算 WSSE Password Digest
        
        用于 ONVIF SOAP 认证：
        Digest = Base64(SHA1(Base64(Nonce) + Created + Password))
        
        Args:
            password (str): 密码
            nonce_b64 (str): Base64 编码的随机数
            created (str): 创建时间
            
        Returns:
            str: Base64 编码的摘要值
        """
        raw = base64.b64decode(nonce_b64) + created.encode('utf-8') + password.encode('utf-8')
        return base64.b64encode(hashlib.sha1(raw).digest()).decode('utf-8')

    def _soap_envelope(self, body_xml, user, pwd):
        """
        构建 SOAP 消息信封
        
        包含 WSSE 安全头和认证信息。
        
        Args:
            body_xml (str): SOAP 消息体
            user (str): 用户名
            pwd (str): 密码
            
        Returns:
            bytes: 编码后的 SOAP 消息
        """
        nonce_b64 = base64.b64encode(uuid.uuid4().bytes).decode('utf-8')
        created = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        digest = self._wsse_digest(pwd, nonce_b64, created)
        return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
<soap:Header>
<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
 xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
<wsse:UsernameToken>
<wsse:Username>{user}</wsse:Username>
<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>
<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
<wsu:Created>{created}</wsu:Created>
</wsse:UsernameToken>
</wsse:Security>
</soap:Header>
<soap:Body>{body_xml}</soap:Body>
</soap:Envelope>'''.encode('utf-8')

    def _soap_call(self, url, body_xml, action, user, pwd, timeout=8):
        """
        执行 SOAP 调用
        
        Args:
            url (str): SOAP 服务端点 URL
            body_xml (str): SOAP 消息体
            action (str): SOAPAction 头部值
            user (str): 用户名
            pwd (str): 密码
            timeout (int): 超时时间（秒）
            
        Returns:
            xml.etree.ElementTree.Element or None: 解析后的响应 XML
        """
        data = self._soap_envelope(body_xml, user, pwd)
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header('Content-Type', 'application/soap+xml; charset=utf-8')
        req.add_header('SOAPAction', action)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return ET.fromstring(resp.read())
        except Exception:
            return None

    def _tag(self, ns, name):
        """
        生成带命名空间的标签名
        
        Args:
            ns (str): 命名空间前缀
            name (str): 标签名
            
        Returns:
            str: 格式为 {namespace}name 的标签
        """
        return f'{{{ONVIF_NS[ns]}}}{name}'

    def _text(self, root, *tags):
        """
        从 XML 树中提取文本值
        
        Args:
            root: XML 根节点
            *tags: 标签路径
            
        Returns:
            str or None: 文本值，未找到返回 None
        """
        for t in tags:
            if isinstance(t, tuple):
                root = root.find(self._tag(*t))
            else:
                root = root.find(t)
            if root is None:
                return None
        return root.text if root is not None else None

    def _onvif_get_osds(self, ip, port, user, pwd):
        """
        通过 ONVIF 获取 OSD 配置列表
        
        调用 GetOSDs 方法获取相机上所有 OSD 配置。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            
        Returns:
            list or None: OSD 配置列表，每项包含 token、位置、文本等信息
        """
        url = f'http://{ip}:{port}/onvif/deviceio_service'
        body = '<tmd:GetOSDs xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl"/>'
        root = self._soap_call(url, body,
                               'http://www.onvif.org/ver10/deviceIO/wsdl/GetOSDs',
                               user, pwd)
        if root is None:
            return None
        
        # 解析 OSD 配置
        osds = root.findall(f'.//{self._tag("tt", "OSD")}')
        result = []
        for osd in osds:
            token = osd.get('token')
            vsc = self._text(osd, f'{self._tag("tt", "VideoSourceConfigurationToken")}')
            osd_type = self._text(osd, f'{self._tag("tt", "Type")}')
            pos_type = self._text(osd, f'{self._tag("tt", "Position")}', f'{self._tag("tt", "Type")}')
            pos_x = self._text(osd, f'{self._tag("tt", "Position")}', f'{self._tag("tt", "Pos")}', f'{self._tag("tt", "x")}')
            pos_y = self._text(osd, f'{self._tag("tt", "Position")}', f'{self._tag("tt", "Pos")}', f'{self._tag("tt", "y")}')
            text = self._text(osd, f'{self._tag("tt", "TextString")}', f'{self._tag("tt", "PlainText")}')
            if token:
                result.append({
                    'token': token,
                    'vsc_token': vsc,
                    'type': osd_type,
                    'pos_type': pos_type,
                    'pos_x': pos_x,
                    'pos_y': pos_y,
                    'text': text,
                })
        return result

    def _onvif_set_osd(self, ip, port, user, pwd, info, new_text='', new_x=None, new_y=None):
        """
        通过 ONVIF 设置 OSD 配置
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            info (dict): OSD 配置信息
            new_text (str): 新的 OSD 文本
            new_x (str): 新的 X 坐标
            new_y (str): 新的 Y 坐标
            
        Returns:
            bool: 操作是否成功
        """
        url = f'http://{ip}:{port}/onvif/deviceio_service'
        vsc = info.get('vsc_token', '')
        ot = info.get('type', 'Text')
        pt = info.get('pos_type', 'Custom')
        px = new_x if new_x is not None else info.get('pos_x', '0.5')
        py = new_y if new_y is not None else info.get('pos_y', '0.5')
        nt = new_text if new_text is not None else (info.get('text') or ' ')
        body = f'''<tmd:SetOSD xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl">
<tmd:OSD token="{info['token']}">
<tt:VideoSourceConfigurationToken xmlns:tt="http://www.onvif.org/ver10/schema">{vsc}</tt:VideoSourceConfigurationToken>
<tt:Type xmlns:tt="http://www.onvif.org/ver10/schema">{ot}</tt:Type>
<tt:Position xmlns:tt="http://www.onvif.org/ver10/schema">
<tt:Type>{pt}</tt:Type>
<tt:Pos>
<tt:x>{px}</tt:x>
<tt:y>{py}</tt:y>
</tt:Pos>
</tt:Position>
<tt:TextString xmlns:tt="http://www.onvif.org/ver10/schema">
<tt:Type>Plain</tt:Type>
<tt:PlainText>{nt}</tt:PlainText>
</tt:TextString>
</tmd:OSD>
</tmd:SetOSD>'''
        root = self._soap_call(url, body,
                               'http://www.onvif.org/ver10/deviceIO/wsdl/SetOSD',
                               user, pwd)
        return root is not None

    def _onvif_disable(self, ip, port, user, pwd):
        """
        通过 ONVIF 禁用 OSD
        
        获取所有 OSD 配置，备份后将其移除（位置设为 -1）。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            
        Returns:
            bool: 操作是否成功
        """
        osds = self._onvif_get_osds(ip, port, user, pwd)
        if not osds:
            return False
        ok = False
        for osd in osds:
            # 备份原始配置
            self._onvif_backup[osd['token']] = osd
            # 通过设置无效位置来禁用 OSD
            if self._onvif_set_osd(ip, port, user, pwd, osd, new_text='', new_x='-1', new_y='-1'):
                ok = True
        return ok

    def _onvif_enable(self, ip, port, user, pwd):
        """
        通过 ONVIF 恢复 OSD
        
        从备份中恢复所有 OSD 配置。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            
        Returns:
            bool: 操作是否成功
        """
        ok = False
        for token, info in self._onvif_backup.items():
            info['token'] = token
            if self._onvif_set_osd(ip, port, user, pwd, info, new_text=info.get('text', ' ')):
                ok = True
        return ok

    # ── HTTP 私有协议实现 ──────────────────────────────────────

    def _http(self, url, user, pwd, data=None, method='GET', content_type=None, timeout=8):
        """
        执行 HTTP 请求
        
        支持 Digest 和 Basic 两种认证方式。
        
        Args:
            url (str): 请求 URL
            user (str): 用户名
            pwd (str): 密码
            data (bytes, optional): 请求体数据
            method (str): HTTP 方法
            content_type (str, optional): Content-Type 头部
            timeout (int): 超时时间（秒）
            
        Returns:
            http.client.HTTPResponse or urllib.error.HTTPError or None
        """
        passman = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        passman.add_password(None, url, user, pwd)
        opener = urllib.request.build_opener(
            urllib.request.HTTPDigestAuthHandler(passman),
            urllib.request.HTTPBasicAuthHandler(passman)
        )
        req = urllib.request.Request(url, data=data, method=method)
        if content_type:
            req.add_header('Content-Type', content_type)
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            return e
        except Exception:
            return None

    def _put_xml(self, url, xml, user, pwd):
        """
        发送 PUT 请求并携带 XML 数据
        
        Args:
            url (str): 请求 URL
            xml (str): XML 内容
            user (str): 用户名
            pwd (str): 密码
            
        Returns:
            bool: 请求是否成功（HTTP 200）
        """
        resp = self._http(url, user, pwd, data=xml.encode('utf-8'), method='PUT', content_type='application/xml')
        return getattr(resp, 'status', 0) == 200

    def _http_disable(self, brand, ip, port, user, pwd, channel):
        """
        通过 HTTP 私有协议禁用 OSD
        
        根据品牌选择对应的实现。
        
        Args:
            brand (str): 相机品牌
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        if brand == '海康威视':
            return self._disable_hikvision(ip, port, user, pwd, channel)
        if brand == '大华':
            return self._disable_dahua(ip, port, user, pwd, channel)
        if brand == '宇视':
            # 宇视使用海康威视协议
            return self._disable_hikvision(ip, port, user, pwd, channel)
        return False

    def _http_enable(self, brand, ip, port, user, pwd, channel):
        """
        通过 HTTP 私有协议恢复 OSD
        
        Args:
            brand (str): 相机品牌
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        if brand == '海康威视':
            return self._enable_hikvision(ip, port, user, pwd, channel)
        if brand == '大华':
            return self._enable_dahua(ip, port, user, pwd, channel)
        if brand == '宇视':
            return self._enable_hikvision(ip, port, user, pwd, channel)
        return False

    def _disable_hikvision(self, ip, port, user, pwd, channel):
        """
        海康威视 OSD 禁用
        
        通过 ISAPI 接口禁用文本叠加。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        url = f'http://{ip}:{port}/ISAPI/System/Video/inputs/channels/{channel}/overlays/text/1'
        xml = '<?xml version="1.0" encoding="UTF-8"?><TextOverlay><id>1</id><enabled>false</enabled></TextOverlay>'
        return self._put_xml(url, xml, user, pwd)

    def _enable_hikvision(self, ip, port, user, pwd, channel):
        """
        海康威视 OSD 恢复
        
        通过 ISAPI 接口启用文本叠加。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        url = f'http://{ip}:{port}/ISAPI/System/Video/inputs/channels/{channel}/overlays/text/1'
        xml = '<?xml version="1.0" encoding="UTF-8"?><TextOverlay><id>1</id><enabled>true</enabled></TextOverlay>'
        return self._put_xml(url, xml, user, pwd)

    def _disable_dahua(self, ip, port, user, pwd, channel):
        """
        大华 OSD 禁用
        
        通过 CGI 接口禁用自定义标题、时间标题和通道标题。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        ci = max(0, int(channel) - 1)
        params = '&'.join([
            f'VideoWidget[{ci}].CustomTitle[0].EncodeBlend=0',
            f'VideoWidget[{ci}].TimeTitle[0].EncodeBlend=0',
            f'VideoWidget[{ci}].ChannelTitle[0].EncodeBlend=0',
        ])
        url = f'http://{ip}:{port}/cgi-bin/configManager.cgi?action=setConfig&{params}'
        resp = self._http(url, user, pwd)
        body = resp.read().decode() if resp and hasattr(resp, 'read') else ''
        return 'OK' in body

    def _enable_dahua(self, ip, port, user, pwd, channel):
        """
        大华 OSD 恢复
        
        通过 CGI 接口启用自定义标题、时间标题和通道标题。
        
        Args:
            ip (str): 相机 IP 地址
            port (str): 相机端口
            user (str): 用户名
            pwd (str): 密码
            channel (str): 通道号
            
        Returns:
            bool: 操作是否成功
        """
        ci = max(0, int(channel) - 1)
        params = '&'.join([
            f'VideoWidget[{ci}].CustomTitle[0].EncodeBlend=1',
            f'VideoWidget[{ci}].TimeTitle[0].EncodeBlend=1',
            f'VideoWidget[{ci}].ChannelTitle[0].EncodeBlend=1',
        ])
        url = f'http://{ip}:{port}/cgi-bin/configManager.cgi?action=setConfig&{params}'
        resp = self._http(url, user, pwd)
        body = resp.read().decode() if resp and hasattr(resp, 'read') else ''
        return 'OK' in body
