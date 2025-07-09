import network
import time
import ujson
import socket
import urequests
import ubinascii
import uhashlib
import neopixel
from machine import Pin

brumisateur = Pin(15, Pin.OUT)
np = neopixel.NeoPixel(Pin(16), 12)
button = Pin(14, mode=Pin.IN, pull=Pin.PULL_UP)
led_power = Pin(0, Pin.OUT)  # LED puissance

state = False
last_button_state = 1  # Pour détecter les changements du bouton

API_URL = "http://api.weatherapi.com/v1/current.json"
API_KEY = "b01876319e9d4a949d0154404242001"
CITY = "San%20Francisco"

# === MÉTÉO & LUMIÈRE ===

def get_weather(city):
    url = f"{API_URL}?q={city}&key={API_KEY}&aqi=no"
    print("Requête :", url)
    try:
        response = urequests.get(url)
        if response.status_code == 200:
            data = response.json()
            response.close()
            return data
        else:
            print("Erreur HTTP :", response.status_code)
            response.close()
            return None
    except Exception as e:
        print("Erreur lors de la récupération météo:", e)
        return None

def switchNeopixel(r, g, b):
    n = np.n
    for i in range(n):
        np[i] = (r, g, b)
    np.write()
    print(f'LEDs allumées en couleur : ({r}, {g}, {b})')
    time.sleep(0.1)

# === CONFIGURATION WI-FI ===

def start_config_portal():
    ssid_ap = "LampeSetup"
    password_ap = "lampe123"

    ap = network.WLAN(network.AP_IF)
    ap.config(essid=ssid_ap, password=password_ap)
    ap.active(True)
    while not ap.active():
        pass
    print("AP actif :", ap.ifconfig())

    html = """<!DOCTYPE html>
                <html>
                <body>
                    <h2>Configuration Wi-Fi</h2>
                    <form method="POST">
                    SSID: <input name="ssid" /><br>
                    Mot de passe: <input name="password" type="password"/><br>
                    <input type="submit" value="Enregistrer"/>
                    </form>
                </body>
                </html>
            """

    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.bind(addr)
    s.listen(1)

    while True:
        cl, addr = s.accept()
        request = cl.recv(1024).decode()

        if "POST" in request and '\r\n\r\n' in request:
            body = request.split('\r\n\r\n', 1)[1]
            parts = body.split('&')
            if len(parts) >= 2 and '=' in parts[0] and '=' in parts[1]:
                ssid = parts[0].split('=')[1].replace('+', ' ').strip()
                password = parts[1].split('=')[1].strip()
                with open("wifi_config.json", "w") as f:
                    f.write(ujson.dumps({"ssid": ssid, "password": password}))

                response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\nConfiguration enregistrée. Relance du script..."
                cl.send(response)
                cl.close()
                print("Configuration enregistrée. Relance du script...")
                return True
            else:
                print("POST reçu mais corps mal formé :", body)
        else:
            cl.send("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n" + html)
            cl.close()

def connect_or_configure():
    try:
        with open("wifi_config.json") as f:
            config = ujson.loads(f.read())
        ssid = config["ssid"]
        password = config["password"]
    except:
        print("Pas de config Wi-Fi.")
        return start_config_portal()

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)

    for _ in range(15):
        if wlan.isconnected():
            print("Connecté :", wlan.ifconfig())
            return False
        time.sleep(1)

    print("Connexion échouée.")
    return start_config_portal()

while True:
    try:
        should_restart = connect_or_configure()
        if not should_restart:
            print("Main lancé : démarrer WebSocket ou autres interactions.")
            break
        else:
            print("Relance du script...")
    except Exception as e:
        print("Erreur :", e)
        time.sleep(5)

# === SERVEUR WEBSOCKET ===

def websocket_handshake(host, port, path):
    import urandom
    key = ubinascii.b2a_base64(bytes([urandom.getrandbits(8) for _ in range(16)])).decode().strip()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    return request.encode()

def send_websocket_frame(sock, message):
    if isinstance(message, str):
        message = message.encode()
    length = len(message)
    header = bytearray([0x81])
    import urandom
    mask = bytes([urandom.getrandbits(8) for _ in range(4)])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.extend([0x80 | 126, length >> 8, length & 0xFF])
    else:
        header.extend([0x80 | 127, 0, 0, 0, 0, length >> 24, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
    header.extend(mask)
    masked_payload = bytearray()
    for i, byte in enumerate(message):
        masked_payload.append(byte ^ mask[i % 4])
    sock.send(header + masked_payload)

def receive_websocket_frame(sock):
    try:
        header = sock.recv(2)
        if len(header) < 2:
            return None
        fin = header[0] & 0x80
        opcode = header[0] & 0x0F
        masked = header[1] & 0x80
        payload_len = header[1] & 0x7F
        if payload_len == 126:
            extended_len = sock.recv(2)
            payload_len = (extended_len[0] << 8) | extended_len[1]
        elif payload_len == 127:
            extended_len = sock.recv(8)
            payload_len = 0
            for i in range(8):
                payload_len = (payload_len << 8) | extended_len[i]
        if masked:
            mask = sock.recv(4)
        payload = sock.recv(payload_len)
        if masked:
            unmasked_payload = bytearray()
            for i, byte in enumerate(payload):
                unmasked_payload.append(byte ^ mask[i % 4])
            payload = bytes(unmasked_payload)
        return payload.decode() if opcode == 1 else payload
    except Exception as e:
        print("Erreur lors de la réception du frame:", e)
        return None

def connect_to_websocket():
    global state, last_button_state
    try:
        addr = socket.getaddrinfo("192.168.249.169", 8765)[0][-1]
        sock = socket.socket()
        sock.connect(addr)
        print("Connecté au serveur WebSocket.")
        handshake_request = websocket_handshake("192.168.249.169", 8765, "/")
        sock.send(handshake_request)
        response = sock.recv(1024)
        print("Réponse handshake:", response.decode())
        if b"101 Switching Protocols" not in response:
            print("Erreur: Handshake WebSocket échoué")
            sock.close()
            return
        print("Handshake WebSocket réussi!")
        lampe_id = "LAMPE123"
        register_message = ujson.dumps({"type": "register", "id": lampe_id})
        send_websocket_frame(sock, register_message)
        print(f"Lampe enregistrée avec l'ID: {lampe_id}")

        while True:
            # === GESTION DU BOUTON ===
            current_button_state = button.value()
            if current_button_state == 0 and last_button_state == 1:
                state = not state
                print(f"Bouton appuyé ! Nouvelle valeur de state: {state}")
                if state:
                    print("Lampe ALLUMÉE (mode opérationnel)")
                    np.fill((0, 255, 0))  # Vert = ON
                    np.write()
                    led_power.value(1)  # Allumer LED puissance
                else:
                    print("Lampe ÉTEINTE (tout désactivé)")
                    np.fill((0, 0, 0))
                    np.write()
                    brumisateur.value(0)
                    led_power.value(0)  # Éteindre LED puissance
            last_button_state = current_button_state

            print("En attente de message")
            message = receive_websocket_frame(sock)
            if message:
                print("Message reçu:", message)
                try:
                    data = ujson.loads(message)
                    if "action" in data:
                        if not state and data["action"] not in ["on"]:
                            print("Lampe éteinte : commande ignorée.")
                            continue

                        print("Etat avant commande : ", state)

                        if data["action"] == "on":
                            print("Commande reçue: Allumer la LED")
                            state = True
                            np.fill((0, 255, 0))
                            np.write()
                            led_power.value(1)  # Allumer LED puissance

                        elif data["action"] == "off":
                            print("Commande reçue: Éteindre la LED")
                            state = False
                            np.fill((0, 0, 0))
                            np.write()
                            brumisateur.value(0)
                            led_power.value(0)  # Éteindre LED puissance

                        elif data["action"] == "ville" and state:
                            print(f"Commande reçue: Changer la ville en {data['value']}")
                            city_raw = data['value']
                            CITY = city_raw.replace(" ", "%20")
                            weather = get_weather(CITY)
                            if weather:
                                condition = weather["current"]["condition"]["text"].lower()
                                location = weather["location"]["name"]
                                temp_c = weather["current"]["temp_c"]
                                print(weather)
                                if "rain" in condition:
                                    print('il pleut')
                                    brumisateur.value(1)
                                    switchNeopixel((0, 0, 255))
                                elif "sunny" in condition or "clear" in condition:
                                    print('il fait beau')
                                    brumisateur.value(0)
                                    switchNeopixel((255, 255, 0))
                                elif "cloudy" in condition:
                                    print('il y a des nuages')
                                    brumisateur.value(0)
                                    switchNeopixel((128, 128, 128))
                                else:
                                    brumisateur.value(0)
                                    switchNeopixel((0, 0, 0))
                                answer = f"Météo à {location}: {condition}, {temp_c}°C"
                                send_websocket_frame(sock, answer)
                            else:
                                send_websocket_frame(sock, "Erreur météo")
                        print("Etat après commande : ", state)
                except ValueError as ve:
                    print("Erreur de format JSON dans le message reçu:", ve)
                    continue
            else:
                print("Connexion fermée par le serveur")
                break
        sock.close()
    except Exception as e:
        print("Erreur WebSocket:", e)

try:
    connect_to_websocket()
except Exception as e:
    print("Erreur WebSocket:", e)
