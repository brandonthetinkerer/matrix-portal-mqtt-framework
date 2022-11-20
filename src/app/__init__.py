import asyncio
import board
from busio import I2C
import gc
import random
from adafruit_matrixportal.matrix import Matrix
from adafruit_bitmap_font import bitmap_font
from adafruit_lis3dh import LIS3DH_I2C
from displayio import Group

from secrets import secrets

from app.constants import (
    DEBUG,
    NETWORK_ENABLE,
    NTP_ENABLE,
    MQTT_ENABLE,
    HASS_ENABLE,
    NTP_INTERVAL,
    MATRIX_WIDTH,
    MATRIX_HEIGHT,
    MATRIX_BIT_DEPTH,
    MATRIX_COLOR_ORDER,
    MQTT_PREFIX,
    ASYNCIO_LOOP_DELAY,
)

from app.storage import store
from app.display import BaseSprite, ClockLabel, load_bitmap
from app.integration import poll_buttons
from app.integration import (
    mqtt_poll,
    on_mqtt_connect,
    on_mqtt_disconnect,
    on_mqtt_message,
    ntp_update,
    ntp_poll,
)
from app.utils import logger, matrix_rotation

logger(
    f"debug={DEBUG} ntp_enable={NTP_ENABLE} ntp_interval={NTP_INTERVAL} mqtt_prefix={MQTT_PREFIX}"
)
logger(
    f"matrix_width={MATRIX_WIDTH} matrix_height={MATRIX_HEIGHT} matrix_bit_depth={MATRIX_BIT_DEPTH} matrix_color_order={MATRIX_COLOR_ORDER}"
)

# CONSTANTS
BUTTON_UP = 0
BUTTON_DOWN = 1

# LOCAL VARS
client = None

# STATIC RESOURCES
logger("loading static resources")
font_bitocra = bitmap_font.load_font("/bitocra7.bdf")
spritesheet, pixel_shader = load_bitmap("/sprites.bmp", transparent_index=31)

# RGB MATRIX
logger("configuring rgb matrix")
matrix = Matrix(
    width=MATRIX_WIDTH,
    height=MATRIX_HEIGHT,
    bit_depth=MATRIX_BIT_DEPTH,
    color_order=MATRIX_COLOR_ORDER,
)
accelerometer = LIS3DH_I2C(I2C(board.SCL, board.SDA), address=0x19)
_ = accelerometer.acceleration  # drain startup readings

# DISPLAY / FRAMEBUFFER
logger("configuring display/framebuffer")
display = matrix.display
display.rotation = matrix_rotation(accelerometer)
display.show(Group())
gc.collect()


# NETWORKING
if NETWORK_ENABLE:
    from adafruit_matrixportal.network import Network

    logger("configuring networking")
    network = Network(status_neopixel=board.NEOPIXEL, debug=DEBUG)
    network.connect()
    mac = network._wifi.esp.MAC_address
    host_id = "{:02x}{:02x}{:02x}{:02x}".format(mac[0], mac[1], mac[2], mac[3])
    gc.collect()

    # NETWORK TIME
    if NTP_ENABLE:
        ntp_update(network)
        gc.collect()

    # MQTT
    if MQTT_ENABLE:
        import adafruit_esp32spi.adafruit_esp32spi_socket as socket
        import adafruit_minimqtt.adafruit_minimqtt as MQTT

        logger("configuring mqtt client")
        MQTT.set_socket(socket, network._wifi.esp)
        client = MQTT.MQTT(
            broker=secrets.get("mqtt_broker"),
            username=secrets.get("mqtt_user"),
            password=secrets.get("mqtt_password"),
            port=secrets.get("mqtt_port", 1883),
        )
        client.on_connect = on_mqtt_connect
        client.on_disconnect = on_mqtt_disconnect
        client.on_message = on_mqtt_message
        client.connect()
        gc.collect()

        # HOME ASSISTANT
        if HASS_ENABLE:
            from app.integration import (
                advertise_entity,
                build_entity_name,
                OPTS_LIGHT_RGB,
            )

            light_rgb_options = dict(
                color_mode=True, supported_color_modes=["rgb"], brightness=False
            )
            advertise_entity(
                client,
                build_entity_name(host_id, "power"),
                "switch",
                dict(),
                dict(state="ON"),
            )
            advertise_entity(
                client,
                build_entity_name(host_id, "date_rgb"),
                "light",
                OPTS_LIGHT_RGB,
                dict(state="ON", color=0x00FF00, brightness=255, color_mode="rgb"),
            )
            gc.collect()

# DISPLAYIO
sprites = []
group = Group()
# Add random sprites
for i in range(4):
    sprite = BaseSprite(
        spritesheet,
        pixel_shader,
        1,
        1,
        16,
        16,
        0,
        random.randint(0, MATRIX_WIDTH - 16),
        random.randint(0, MATRIX_WIDTH - 16),
        async_delay=0.001,
    )
    sprite.set_velocity(random.randint(-1, 1), random.randint(-1, 1))
    sprites.append(sprite)
    group.append(sprite.get_tilegrid())
# Add clock
clock = ClockLabel(x=1, y=3, font=font_bitocra, async_delay=0.5)
group.append(clock)
# Show group
display.show(group)

# EVENT LOOP
def run():
    logger("start asyncio event loop")
    gc.collect()
    while True:
        try:
            asyncio.run(main())
        finally:
            logger(f"asyncio crash, restarting")
            asyncio.new_event_loop()


async def main():
    logger("event loop started")
    asyncio.create_task(poll_buttons())
    if MQTT_ENABLE:
        asyncio.create_task(mqtt_poll(client))
    if NTP_ENABLE:
        asyncio.create_task(ntp_poll(network))
    asyncio.create_task(clock.start())
    for sprite in sprites:
        asyncio.create_task(sprite.start())
    gc.collect()
    while True:
        asyncio.create_task(tick())
        await asyncio.sleep(ASYNCIO_LOOP_DELAY)
        gc.collect()


async def tick():
    global store, sprite
    frame = store["frame"]
    logger(f"tick: frame={frame}")
    for sprite in sprites:
        if frame % 80 == 0:
            sprite.set_velocity(random.randint(-1, 2), random.randint(-1, 2))
        if sprite.x < 0:
            sprite.set_velocity(x=1)
        elif sprite.x > MATRIX_WIDTH - 16:
            sprite.set_velocity(x=-1)
        if sprite.y < 0:
            sprite.set_velocity(y=1)
        elif sprite.y > MATRIX_HEIGHT - 16:
            sprite.set_velocity(y=-1)
    store["frame"] += 1


# STARTUP

run()
