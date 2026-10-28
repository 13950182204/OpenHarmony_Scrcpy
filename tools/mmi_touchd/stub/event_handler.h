/*
 * Minimal stub for the OpenHarmony eventhandler header.
 *
 * The public multimodalinput headers forward-declare OHOS::AppExecFwk::EventHandler
 * but the packaged tree does not ship foundation/eventhandler. Only the
 * declaration is needed to compile against InputManager::SimulateInputEvent.
 */
#ifndef OHSCRCPY_EVENT_HANDLER_STUB_H
#define OHSCRCPY_EVENT_HANDLER_STUB_H

namespace OHOS {
namespace AppExecFwk {
class EventHandler;
}  // namespace AppExecFwk
}  // namespace OHOS

#endif  // OHSCRCPY_EVENT_HANDLER_STUB_H
