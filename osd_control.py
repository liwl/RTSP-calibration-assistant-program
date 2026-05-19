import base64
import hashlib
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from xml.etree import ElementTree as ET

ONVIF_NS = {
    'soap': 'http://www.w3.org/2003/05/soap-envelope',
    'wsse': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd',
    'wsu': 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd',
    'tds': 'http://www.onvif.org/ver10/device/wsdl',
    'tmd': 'http://www.onvif.org/ver10/deviceIO/wsdl',
    'tt': 'http://www.onvif.org/ver10/schema',
}


class OSDController:
    def __init__(self):
        self._disabled = False
        self._onvif_backup = {}

    @property
    def is_disabled(self):
        return self._disabled

    def disable_osd(self, brand, ip, port, user, pwd, channel):
        if self._onvif_disable(ip, port, user, pwd):
            self._disabled = True
            return True
        ok = self._http_disable(brand, ip, port, user, pwd, channel)
        if ok:
            self._disabled = True
        return ok

    def enable_osd(self, brand, ip, port, user, pwd, channel):
        if self._onvif_backup and self._onvif_enable(ip, port, user, pwd):
            self._disabled = False
            self._onvif_backup = {}
            return True
        ok = self._http_enable(brand, ip, port, user, pwd, channel)
        if ok:
            self._disabled = False
        return ok

    # ── ONVIF ──────────────────────────────────────────────

    def _wsse_digest(self, password, nonce_b64, created):
        raw = base64.b64decode(nonce_b64) + created.encode('utf-8') + password.encode('utf-8')
        return base64.b64encode(hashlib.sha1(raw).digest()).decode('utf-8')

    def _soap_envelope(self, body_xml, user, pwd):
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
        return f'{{{ONVIF_NS[ns]}}}{name}'

    def _text(self, root, *tags):
        for t in tags:
            if isinstance(t, tuple):
                root = root.find(self._tag(*t))
            else:
                root = root.find(t)
            if root is None:
                return None
        return root.text if root is not None else None

    def _onvif_get_osds(self, ip, port, user, pwd):
        url = f'http://{ip}:{port}/onvif/deviceio_service'
        body = '<tmd:GetOSDs xmlns:tmd="http://www.onvif.org/ver10/deviceIO/wsdl"/>'
        root = self._soap_call(url, body,
                               'http://www.onvif.org/ver10/deviceIO/wsdl/GetOSDs',
                               user, pwd)
        if root is None:
            return None
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
        osds = self._onvif_get_osds(ip, port, user, pwd)
        if not osds:
            return False
        ok = False
        for osd in osds:
            self._onvif_backup[osd['token']] = osd
            if self._onvif_set_osd(ip, port, user, pwd, osd, new_text='', new_x='-1', new_y='-1'):
                ok = True
        return ok

    def _onvif_enable(self, ip, port, user, pwd):
        ok = False
        for token, info in self._onvif_backup.items():
            info['token'] = token
            if self._onvif_set_osd(ip, port, user, pwd, info, new_text=info.get('text', ' ')):
                ok = True
        return ok

    # ── HTTP private protocol fallback ──────────────────────

    def _http(self, url, user, pwd, data=None, method='GET', content_type=None, timeout=8):
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
        resp = self._http(url, user, pwd, data=xml.encode('utf-8'), method='PUT', content_type='application/xml')
        return getattr(resp, 'status', 0) == 200

    def _http_disable(self, brand, ip, port, user, pwd, channel):
        if brand == '海康威视':
            return self._disable_hikvision(ip, port, user, pwd, channel)
        if brand == '大华':
            return self._disable_dahua(ip, port, user, pwd, channel)
        if brand == '宇视':
            return self._disable_hikvision(ip, port, user, pwd, channel)
        return False

    def _http_enable(self, brand, ip, port, user, pwd, channel):
        if brand == '海康威视':
            return self._enable_hikvision(ip, port, user, pwd, channel)
        if brand == '大华':
            return self._enable_dahua(ip, port, user, pwd, channel)
        if brand == '宇视':
            return self._enable_hikvision(ip, port, user, pwd, channel)
        return False

    def _disable_hikvision(self, ip, port, user, pwd, channel):
        url = f'http://{ip}:{port}/ISAPI/System/Video/inputs/channels/{channel}/overlays/text/1'
        xml = '<?xml version="1.0" encoding="UTF-8"?><TextOverlay><id>1</id><enabled>false</enabled></TextOverlay>'
        return self._put_xml(url, xml, user, pwd)

    def _enable_hikvision(self, ip, port, user, pwd, channel):
        url = f'http://{ip}:{port}/ISAPI/System/Video/inputs/channels/{channel}/overlays/text/1'
        xml = '<?xml version="1.0" encoding="UTF-8"?><TextOverlay><id>1</id><enabled>true</enabled></TextOverlay>'
        return self._put_xml(url, xml, user, pwd)

    def _disable_dahua(self, ip, port, user, pwd, channel):
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
