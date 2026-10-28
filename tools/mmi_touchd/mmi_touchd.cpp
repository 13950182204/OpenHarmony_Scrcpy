/*
 * Copyright (c) 2026 luodh0157.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * mmi_touchd - OpenHarmony remote touch injection daemon.
 *
 * /bin/uinput starts a process and connects to the multimodalinput SA for
 * every single event, which costs roughly 340 ms per tap and makes remote
 * dragging unusable. This daemon stays resident on the device and forwards
 * touch events from the client straight into
 * InputManager::SimulateInputEvent(), which costs a few milliseconds.
 *
 * Usage: mmi_touchd [port]   (default port 27196, loopback only)
 *
 * Line protocol, one request per line, one reply per line:
 *   D <x> <y>  pointer down     -> ok <cost-ms>
 *   M <x> <y>  pointer move     -> ok <cost-ms>
 *   U <x> <y>  pointer up       -> ok <cost-ms>
 *   R          lift at last pos -> ok <cost-ms>
 *   P          ping             -> ok 0
 *   S          status           -> ok 1 <events> <pid>
 *   Q          quit connection  -> bye
 *
 * Coordinates are physical display pixels (the same space /bin/uinput uses).
 */

/*
 * The OpenHarmony multimodalinput headers rely on STL declarations that are
 * only complete when the standard headers are included first; without this
 * prologue input_manager.h fails with "too few template arguments for class
 * template 'map'". Keep the prologue above every project include.
 */
#include <map>
#include <tuple>
#include <vector>
#include <string>
#include <functional>
#include <memory>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cerrno>
#include <unistd.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>

#include "input_manager.h"
#include "pointer_event.h"

using namespace OHOS::MMI;

namespace {
constexpr int DEFAULT_PORT = 27196;
constexpr int LISTEN_BACKLOG = 4;
constexpr int STATUS_LOG_INTERVAL = 300;

int64_t NowMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

int64_t g_downTime = 0;
int32_t g_lastX = 0;
int32_t g_lastY = 0;
int64_t g_eventCount = 0;

/* Inject one pointer event and return the injection cost in milliseconds. */
int SendEvent(int32_t action, int32_t x, int32_t y, bool pressed)
{
    auto pointerEvent = PointerEvent::Create();
    if (pointerEvent == nullptr) {
        return -1;
    }
    if (action == PointerEvent::POINTER_ACTION_DOWN) {
        g_downTime = NowMs();
    }
    pointerEvent->SetPointerAction(action);
    pointerEvent->SetSourceType(PointerEvent::SOURCE_TYPE_TOUCHSCREEN);
    pointerEvent->SetPointerId(0);
    pointerEvent->SetButtonId(0);
    pointerEvent->SetButtonPressed(pressed ? 1 : 0);
    pointerEvent->SetActionTime(NowMs());

    PointerEvent::PointerItem item;
    item.SetPointerId(0);
    item.SetDisplayX(x);
    item.SetDisplayY(y);
    item.SetRawDisplayX(x);
    item.SetRawDisplayY(y);
    item.SetPressed(pressed);
    item.SetDownTime(g_downTime);
    item.SetPressure(pressed ? 0.5 : 0.0);
    pointerEvent->AddPointerItem(item);

    int64_t started = NowMs();
    InputManager::GetInstance()->SimulateInputEvent(pointerEvent);
    return static_cast<int>(NowMs() - started);
}

bool WriteAll(int fd, const char *text)
{
    size_t length = strlen(text);
    size_t sent = 0;
    while (sent < length) {
        ssize_t written = write(fd, text + sent, length - sent);
        if (written <= 0) {
            return false;
        }
        sent += static_cast<size_t>(written);
    }
    return true;
}

/* Serve one client connection until it closes or sends Q. */
void ServeClient(int fd)
{
    char buffer[4096];
    size_t used = 0;
    while (true) {
        ssize_t readSize = read(fd, buffer + used, sizeof(buffer) - used - 1);
        if (readSize <= 0) {
            return;
        }
        used += static_cast<size_t>(readSize);
        buffer[used] = '\0';

        char *line = buffer;
        while (true) {
            char *newline = strchr(line, '\n');
            if (newline == nullptr) {
                break;
            }
            *newline = '\0';
            char reply[96];
            int x = 0;
            int y = 0;
            char command = line[0];
            if (command == 'Q') {
                WriteAll(fd, "bye\n");
                return;
            } else if (command == 'P') {
                snprintf(reply, sizeof(reply), "ok 0\n");
            } else if (command == 'S') {
                snprintf(reply, sizeof(reply), "ok 1 %lld %d\n",
                         static_cast<long long>(g_eventCount), static_cast<int>(getpid()));
            } else if (command == 'R') {
                int cost = SendEvent(PointerEvent::POINTER_ACTION_UP, g_lastX, g_lastY, false);
                snprintf(reply, sizeof(reply), "ok %d\n", cost);
            } else if (sscanf(line + 1, "%d %d", &x, &y) == 2 &&
                       (command == 'D' || command == 'M' || command == 'U')) {
                int32_t action = PointerEvent::POINTER_ACTION_DOWN;
                bool pressed = true;
                if (command == 'M') {
                    action = PointerEvent::POINTER_ACTION_MOVE;
                } else if (command == 'U') {
                    action = PointerEvent::POINTER_ACTION_UP;
                    pressed = false;
                }
                g_lastX = x;
                g_lastY = y;
                int cost = SendEvent(action, x, y, pressed);
                ++g_eventCount;
                if (g_eventCount % STATUS_LOG_INTERVAL == 0) {
                    printf("events=%lld last_cost=%dms\n",
                           static_cast<long long>(g_eventCount), cost);
                }
                snprintf(reply, sizeof(reply), "ok %d\n", cost);
            } else {
                snprintf(reply, sizeof(reply), "err\n");
            }
            if (!WriteAll(fd, reply)) {
                return;
            }
            line = newline + 1;
        }

        size_t remain = strlen(line);
        memmove(buffer, line, remain);
        used = remain;
        if (used >= sizeof(buffer) - 1) {
            /* A single oversized line is not a valid request; drop it. */
            used = 0;
        }
    }
}
}  // namespace

int main(int argc, char **argv)
{
    int port = DEFAULT_PORT;
    if (argc >= 2) {
        port = atoi(argv[1]);
    }
    if (port <= 0 || port > 65535) {
        printf("invalid port\n");
        return 1;
    }

    signal(SIGPIPE, SIG_IGN);
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) {
        printf("socket failed errno=%d\n", errno);
        return 1;
    }
    int enable = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &enable, sizeof(enable));

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<uint16_t>(port));
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(server, reinterpret_cast<struct sockaddr *>(&address), sizeof(address)) < 0) {
        printf("bind failed port=%d errno=%d\n", port, errno);
        return 1;
    }
    if (listen(server, LISTEN_BACKLOG) < 0) {
        printf("listen failed errno=%d\n", errno);
        return 1;
    }

    setvbuf(stdout, nullptr, _IONBF, 0);
    printf("mmi_touchd listening %d pid=%d\n", port, static_cast<int>(getpid()));

    while (true) {
        struct sockaddr_in clientAddress;
        socklen_t clientLength = sizeof(clientAddress);
        int client = accept(server, reinterpret_cast<struct sockaddr *>(&clientAddress), &clientLength);
        if (client < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &enable, sizeof(enable));
        ServeClient(client);
        close(client);
    }
    close(server);
    return 0;
}
