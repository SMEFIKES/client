import asyncio

import pyglet


class CustomEventLoop(pyglet.app.EventLoop):
    async def run(self):
        """Begin processing events, scheduled functions and window updates.

        This method returns when :py:attr:`has_exit` is set to True.

        Developers are discouraged from overriding this method, as the
        implementation is platform-specific.
        """
        self.has_exit = False
        self._legacy_setup()

        platform_event_loop = pyglet.app.platform_event_loop
        platform_event_loop.start()
        self.dispatch_event('on_enter')

        await self.websocket_client.send_json(
            {'action': 'connect', 'username': self.main_window.settings['username']}
        )

        self.is_running = True
        await self._run()

        self.is_running = False
        self.dispatch_event('on_exit')
        platform_event_loop.stop()
        raise asyncio.CancelledError

    async def _run(self):
        """The simplest standard run loop, using constant timeout.  Suitable
        for well-behaving platforms (Mac, Linux and some Windows).
        """
        platform_event_loop = pyglet.app.platform_event_loop
        while not self.has_exit:
            timeout = await self.idle()
            platform_event_loop.step(timeout)

    async def idle(self):
        """Called during each iteration of the event loop.

        The method is called immediately after any window events (i.e., after
        any user input).  The method can return a duration after which
        the idle method will be called again.  The method may be called
        earlier if the user creates more input events.  The method
        can return `None` to only wait for user events.

        For example, return ``1.0`` to have the idle method called every
        second, or immediately after any user events.

        The default implementation dispatches the
        :py:meth:`pyglet.window.Window.on_draw` event for all windows and uses
        :py:func:`pyglet.clock.tick` and :py:func:`pyglet.clock.get_sleep_time`
        on the default clock to determine the return value.

        This method should be overridden by advanced users only.  To have
        code execute at regular intervals, use the
        :py:func:`pyglet.clock.schedule` methods.

        :rtype: float
        :return: The number of seconds before the idle method should
            be called again, or `None` to block for user input.
        """
        dt = self.clock.update_time()
        redraw_all = self.clock.call_scheduled_functions(dt)

        # Redraw all windows
        window = self.main_window
        if redraw_all or (window._legacy_invalid and window.invalid):
            window.switch_to()
            window.dispatch_event('on_draw')
            window.flip()
            window._legacy_invalid = False

        while window.ws_messages_queue:
            message = window.ws_messages_queue.pop()
            await self.websocket_client.send_json(message)

        # Update timout
        sleep_time = self.clock.get_sleep_time(True)
        if not sleep_time:
            sleep_time = 1.0/60
        await asyncio.sleep(sleep_time)
        return 0
