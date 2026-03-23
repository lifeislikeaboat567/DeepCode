"""Reflex application entrypoint for the DeepCode Web UI."""

from __future__ import annotations

import reflex as rx

from deepcode_reflex.state import ChatSessionGroup, TaskDialogGroup, UIState


def _status_badge(status: str) -> rx.Component:
    return rx.text(status, class_name=f"dc-badge dc-status-{status}")


def _card(title: str, body: rx.Component, header_right: rx.Component | None = None, class_name: str = "") -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(title, class_name="dc-card-title"),
            header_right if header_right is not None else rx.spacer(),
            justify="between",
            align="center",
            width="100%",
            class_name="dc-card-header",
        ),
        body,
        class_name=f"dc-card {class_name}".strip(),
    )


def _kpi_card(item: dict[str, str]) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(item["title"], class_name="dc-kpi-title"),
            rx.text(item["delta"], class_name=item["pill_class"]),
            justify="between",
            width="100%",
        ),
        rx.text(item["value"], class_name="dc-kpi-value"),
        class_name="dc-kpi-card",
    )


def _page_header() -> rx.Component:
    return rx.hstack(
        rx.box(
            rx.text(UIState.current_page_title, class_name="dc-page-title"),
            rx.text(UIState.current_page_subtitle, class_name="dc-page-subtitle"),
            class_name="dc-page-header-left",
        ),
        rx.box(
            rx.text(UIState.i18n["header.updated"], class_name="dc-page-status-kicker"),
            rx.hstack(
                rx.text(UIState.i18n["header.live"], class_name="dc-page-status-value-prefix"),
                rx.text("·", class_name="dc-page-status-value-prefix"),
                rx.moment(format="YYYY-MM-DD HH:mm:ss", interval=1000, class_name="dc-page-status-value-time"),
                class_name="dc-page-status-value",
                spacing="1",
            ),
            class_name="dc-page-header-right",
        ),
        justify="between",
        width="100%",
        class_name="dc-page-header",
    )


def sidebar() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.cond(
                UIState.sidebar_collapsed,
                rx.fragment(),
                rx.box(
                    rx.text(UIState.i18n["sidebar.kicker"], class_name="dc-kicker"),
                    rx.heading(UIState.i18n["sidebar.title"], size="6"),
                    rx.text(UIState.i18n["sidebar.tagline"], class_name="dc-muted"),
                    class_name="dc-brand",
                ),
            ),
            rx.button(
                rx.cond(
                    UIState.sidebar_collapsed,
                    rx.icon(tag="chevron_right", class_name="dc-sidebar-toggle-icon"),
                    rx.icon(tag="chevron_left", class_name="dc-sidebar-toggle-icon"),
                ),
                on_click=UIState.toggle_sidebar,
                class_name=rx.cond(
                    UIState.sidebar_collapsed,
                    "dc-sidebar-toggle is-collapsed",
                    "dc-sidebar-toggle",
                ),
            ),
            justify=rx.cond(UIState.sidebar_collapsed, "center", "between"),
            align="start",
            width="100%",
            class_name="dc-sidebar-top",
        ),
        rx.cond(
            UIState.sidebar_collapsed,
            rx.fragment(),
            rx.box(
                rx.button(UIState.i18n["lang.zh"], on_click=lambda: UIState.set_language("zh"), class_name="dc-lang-btn dc-lang-btn-zh"),
                rx.button("EN", on_click=lambda: UIState.set_language("en"), class_name="dc-lang-btn dc-lang-btn-en"),
                class_name=rx.cond(
                    UIState.ui_language == "en",
                    "dc-lang-switch is-en",
                    "dc-lang-switch is-zh",
                ),
            ),
        ),
        rx.box(
            rx.foreach(
                UIState.nav_rows,
                lambda item: rx.button(
                    rx.hstack(
                        rx.icon(tag=item["icon"], class_name="dc-nav-icon"),
                        rx.cond(UIState.sidebar_collapsed, rx.fragment(), rx.text(item["label"], class_name="dc-nav-label")),
                        width="100%",
                        justify=rx.cond(UIState.sidebar_collapsed, "center", "start"),
                        spacing="2",
                    ),
                    on_click=lambda: UIState.set_selected_page(item["id"]),
                    class_name=rx.cond(
                        UIState.sidebar_collapsed,
                        rx.cond(
                            UIState.selected_page == item["id"],
                            "dc-nav-btn dc-nav-btn-active dc-nav-btn-collapsed",
                            "dc-nav-btn dc-nav-btn-collapsed",
                        ),
                        rx.cond(
                            UIState.selected_page == item["id"],
                            "dc-nav-btn dc-nav-btn-active",
                            "dc-nav-btn",
                        ),
                    ),
                ),
            ),
            class_name="dc-nav-list",
        ),
        rx.cond(
            UIState.sidebar_collapsed,
            rx.vstack(
                rx.button(rx.icon(tag="refresh_cw", class_name="dc-nav-icon"), on_click=UIState.refresh_data_with_feedback, class_name="dc-sidebar-icon-action"),
                rx.button(rx.icon(tag="square_pen", class_name="dc-nav-icon"), on_click=lambda: [UIState.set_selected_page("task_center")], class_name="dc-sidebar-icon-action"),
                class_name="dc-sidebar-actions-collapsed",
            ),
            rx.hstack(
                rx.button(UIState.i18n["sidebar.refresh"], on_click=UIState.refresh_data_with_feedback, class_name="dc-cta dc-sidebar-action-btn"),
                rx.button(
                    UIState.i18n["sidebar.cta.button"],
                    on_click=lambda: [UIState.set_selected_page("task_center")],
                    class_name="dc-cta dc-cta-ghost dc-sidebar-action-btn",
                ),
                class_name="dc-sidebar-actions",
                justify="center",
                width="100%",
                spacing="4",
            ),
        ),
        class_name=rx.cond(UIState.sidebar_collapsed, "dc-sidebar dc-sidebar-collapsed", "dc-sidebar"),
    )


def _dashboard_range_select() -> rx.Component:
    return rx.select(
        UIState.dashboard_trend_range_options,
        value=UIState.dashboard_trend_range_label,
        on_change=UIState.set_dashboard_trend_range,
        class_name="dc-trend-select",
    )


def dashboard_page() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.box(
                rx.box(
                    rx.foreach(UIState.dashboard_metric_cards, _kpi_card),
                    class_name="dc-kpi-grid",
                ),
                _card(
                    UIState.i18n["dashboard.trend.title"],
                    rx.vstack(
                        rx.recharts.line_chart(
                            rx.recharts.cartesian_grid(stroke_dasharray="3 3", vertical=False, stroke="#DEE6F3"),
                            rx.recharts.x_axis(data_key="label", axis_line=False, tick_line=False, stroke="#8F95B2"),
                            rx.recharts.y_axis(allow_decimals=False, axis_line=False, tick_line=False, stroke="#8F95B2", width=36),
                            rx.recharts.tooltip(),
                            rx.recharts.line(
                                data_key="count",
                                type_="monotone",
                                stroke="#3D5AFE",
                                stroke_width=2.6,
                                dot=False,
                            ),
                            data=UIState.dashboard_trend_points,
                            class_name="dc-trend-chart",
                            height=308,
                        ),
                        spacing="4",
                        width="100%",
                    ),
                    header_right=_dashboard_range_select(),
                    class_name="dc-dashboard-card dc-dashboard-trend-card",
                ),
                class_name="dc-dashboard-main dc-dashboard-main-grid",
            ),
            rx.box(
                _card(
                    UIState.i18n["dashboard.mix.title"],
                    rx.vstack(
                        rx.box(
                            rx.box(class_name="dc-donut-ring", background=UIState.dashboard_donut_background),
                            rx.box(
                                rx.text(UIState.dashboard_filtered_total, class_name="dc-donut-value"),
                                rx.text(UIState.i18n["common.task"], class_name="dc-donut-label"),
                                class_name="dc-donut-center",
                            ),
                            class_name="dc-donut-wrap",
                        ),
                        rx.foreach(
                            UIState.dashboard_status_rows,
                            lambda row: rx.hstack(
                                rx.box(class_name="dc-status-dot", background=row["color"]),
                                rx.text(row["label"], class_name="dc-muted"),
                                rx.spacer(),
                                rx.text(row["pct"], class_name="dc-muted"),
                                width="100%",
                                align="center",
                            ),
                        ),
                        spacing="3",
                    ),
                    header_right=_dashboard_range_select(),
                    class_name="dc-dashboard-card dc-dashboard-side-card",
                ),
                _card(
                    UIState.i18n["dashboard.channel.title"],
                    rx.box(
                        rx.foreach(
                            UIState.dashboard_status_rows,
                            lambda row: rx.box(
                                rx.hstack(
                                    rx.text(row["label"], class_name="dc-trend-label"),
                                    rx.text(f"{row['count']} · {row['pct']}", class_name="dc-trend-count"),
                                    justify="between",
                                    width="100%",
                                ),
                                rx.box(
                                    rx.box(class_name="dc-dist-fill", width=row["width"], background=row["color"]),
                                    class_name="dc-dist-track",
                                ),
                                class_name="dc-dist-row",
                            ),
                        ),
                        class_name="dc-dist-panel",
                    ),
                    header_right=_dashboard_range_select(),
                    class_name="dc-dashboard-card dc-dashboard-side-card",
                ),
                class_name="dc-dashboard-side dc-dashboard-side-grid",
            ),
            class_name="dc-dashboard-grid",
        ),
        _card(
            UIState.i18n["dashboard.recent_tasks"],
            rx.box(
                rx.cond(
                    UIState.tasks.length() > 0,
                    rx.foreach(
                        UIState.paginated_tasks,
                        lambda row: rx.box(
                            rx.hstack(
                                rx.text(row["id"][:8], class_name="dc-id"),
                                _status_badge(row["status"]),
                                justify="between",
                                width="100%",
                            ),
                            rx.text(row["task"]),
                            rx.cond(
                                UIState.ui_language == "en",
                                rx.text(f"Updated {row['updated_at']} · Artifacts {row['artifacts']}", class_name="dc-muted"),
                                rx.text(f"更新于 {row['updated_at']} · 工件 {row['artifacts']}", class_name="dc-muted"),
                            ),
                            class_name="dc-list-item dc-clickable-row",
                            on_click=lambda: UIState.open_task_detail(row["id"]),
                        ),
                    ),
                    rx.text(UIState.i18n["dashboard.no_tasks"], class_name="dc-muted"),
                ),
            ),
        ),
        width="100%",
        spacing="4",
    )


def _chat_dialog_item(row: dict[str, str]) -> rx.Component:
    return rx.box(
        rx.button(
            rx.vstack(
                rx.hstack(
                    rx.text(row["name"], class_name="dc-dialog-title"),
                    rx.cond(
                        row["is_pinned"] == "1",
                        rx.hstack(rx.icon(tag="pin", class_name="dc-nav-icon"), rx.text("置顶", class_name="dc-dialog-meta"), spacing="1"),
                        rx.fragment(),
                    ),
                    width="100%",
                    justify="between",
                ),
                rx.text(f"{row['id'][:8]} · {row['messages']} messages", class_name="dc-dialog-meta"),
                align="start",
                spacing="1",
                width="100%",
                class_name="dc-dialog-text-wrap",
            ),
            on_click=lambda: UIState.select_session(row["id"]),
            class_name=rx.cond(
                UIState.selected_session_id == row["id"],
                "dc-dialog-select-btn dc-dialog-select-btn-active",
                "dc-dialog-select-btn",
            ),
        ),
        rx.dropdown_menu.root(
            rx.dropdown_menu.trigger(
                rx.button(
                    rx.icon(tag="ellipsis", class_name="dc-dialog-action-icon"),
                    class_name="dc-dialog-action-trigger",
                )
            ),
            rx.dropdown_menu.content(
                rx.dropdown_menu.item(
                    rx.hstack(
                        rx.icon(tag="square_pen", class_name="dc-nav-icon"),
                        rx.text("重命名"),
                        spacing="2",
                    ),
                    on_select=lambda: UIState.start_rename_session(row["id"], row["name"]),
                    class_name="dc-dialog-menu-item",
                ),
                rx.dropdown_menu.item(
                    rx.hstack(
                        rx.icon(
                            tag=rx.cond(row["is_pinned"] == "1", "pin_off", "pin"),
                            class_name="dc-nav-icon",
                        ),
                        rx.text(rx.cond(row["is_pinned"] == "1", "取消置顶", "置顶")),
                        spacing="2",
                    ),
                    on_select=lambda: UIState.toggle_pin_session(row["id"]),
                    class_name="dc-dialog-menu-item",
                ),
                rx.dropdown_menu.separator(class_name="dc-dialog-menu-separator"),
                rx.dropdown_menu.item(
                    rx.hstack(
                        rx.icon(tag="trash_2", class_name="dc-nav-icon"),
                        rx.text("删除"),
                        spacing="2",
                    ),
                    on_select=lambda: UIState.request_delete_session(row["id"]),
                    class_name="dc-dialog-menu-item dc-dialog-menu-item-danger",
                ),
                class_name="dc-dialog-menu-content",
                side="bottom",
                align="end",
                side_offset=6,
            ),
            class_name="dc-dialog-action-root",
        ),
        class_name=rx.cond(
            UIState.selected_session_id == row["id"],
            "dc-dialog-item dc-dialog-item-selected",
            "dc-dialog-item",
        ),
    )


def _session_delete_modal() -> rx.Component:
    return rx.cond(
        (UIState.session_delete_confirm_id != "") | (UIState.session_delete_confirm_group_key != ""),
        rx.box(
            rx.box(class_name="dc-session-modal-backdrop", on_click=UIState.cancel_delete_session),
            rx.box(
                rx.vstack(
                    rx.cond(
                        UIState.session_delete_confirm_group_key != "",
                        rx.text("永久删除该日期分组", class_name="dc-session-modal-title"),
                        rx.text("永久删除对话", class_name="dc-session-modal-title"),
                    ),
                    rx.cond(
                        UIState.session_delete_confirm_group_key != "",
                        rx.text(
                            f"将删除“{UIState.session_delete_confirm_group_title}”分组下的 {UIState.session_delete_confirm_group_count} 条对话记录，且不可恢复。确认继续吗？",
                            class_name="dc-session-modal-warning",
                        ),
                        rx.text("删除后，该对话将不可恢复。确认删除吗？", class_name="dc-session-modal-warning"),
                    ),
                    rx.hstack(
                        rx.button(
                            rx.cond(UIState.session_delete_confirm_group_key != "", "删除本组", "确认"),
                            on_click=UIState.delete_requested_session,
                            class_name="dc-small-btn dc-session-modal-btn dc-session-modal-btn-danger",
                        ),
                        rx.button(
                            "取消",
                            on_click=UIState.cancel_delete_session,
                            class_name="dc-small-btn dc-session-modal-btn",
                        ),
                        spacing="3",
                        width="100%",
                        justify="end",
                        class_name="dc-session-modal-actions",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                class_name="dc-session-modal-card",
            ),
            class_name="dc-session-modal-layer",
        ),
        rx.fragment(),
    )


def _skill_delete_modal() -> rx.Component:
    return rx.cond(
        UIState.skill_delete_confirm_path != "",
        rx.box(
            rx.box(class_name="dc-session-modal-backdrop", on_click=UIState.cancel_delete_skill),
            rx.box(
                rx.vstack(
                    rx.text("删除 Skill", class_name="dc-session-modal-title"),
                    rx.text(
                        "将永久删除以下 Skill，该操作不可恢复。确认继续吗？",
                        class_name="dc-session-modal-warning",
                    ),
                    rx.text(
                        rx.cond(
                            UIState.skill_delete_confirm_name != "",
                            UIState.skill_delete_confirm_name,
                            UIState.skill_delete_confirm_path,
                        ),
                        class_name="dc-id",
                    ),
                    rx.hstack(
                        rx.button(
                            "确认删除",
                            on_click=UIState.delete_requested_skill,
                            class_name="dc-small-btn dc-session-modal-btn dc-session-modal-btn-danger",
                        ),
                        rx.button(
                            "取消",
                            on_click=UIState.cancel_delete_skill,
                            class_name="dc-small-btn dc-session-modal-btn",
                        ),
                        spacing="3",
                        width="100%",
                        justify="end",
                        class_name="dc-session-modal-actions",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                class_name="dc-session-modal-card",
            ),
            class_name="dc-session-modal-layer",
        ),
        rx.fragment(),
    )


def _session_rename_modal() -> rx.Component:
    return rx.cond(
        UIState.session_rename_id != "",
        rx.box(
            rx.box(class_name="dc-session-modal-backdrop", on_click=UIState.cancel_session_rename),
            rx.box(
                rx.vstack(
                    rx.text("修改对话标题", class_name="dc-session-modal-title"),
                    rx.input(
                        value=UIState.session_rename_value,
                        on_change=UIState.set_session_rename_value,
                        placeholder="输入对话名称",
                        class_name="dc-input dc-session-modal-input",
                    ),
                    rx.hstack(
                        rx.button(
                            "保存",
                            on_click=UIState.save_session_rename,
                            class_name="dc-small-btn dc-session-modal-btn dc-session-modal-btn-primary",
                        ),
                        rx.button(
                            "取消",
                            on_click=UIState.cancel_session_rename,
                            class_name="dc-small-btn dc-session-modal-btn",
                        ),
                        spacing="3",
                        width="100%",
                        justify="end",
                        class_name="dc-session-modal-actions",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                class_name="dc-session-modal-card",
            ),
            class_name="dc-session-modal-layer",
        ),
        rx.fragment(),
    )


def _task_dialog_item(row: dict[str, str]) -> rx.Component:
    return rx.box(
        rx.button(
            rx.vstack(
                rx.hstack(
                    rx.text(row["task"], class_name="dc-task-dialog-title"),
                    rx.cond(
                        row["is_pinned"] == "1",
                        rx.hstack(rx.icon(tag="pin", class_name="dc-nav-icon"), rx.text("置顶", class_name="dc-task-dialog-meta"), spacing="1"),
                        rx.fragment(),
                    ),
                    width="100%",
                    justify="between",
                ),
                rx.text(f"{row['status']} · {row['updated_at']}", class_name="dc-task-dialog-meta"),
                align="start",
                spacing="1",
                width="100%",
                class_name="dc-task-dialog-text-wrap",
            ),
            on_click=lambda: UIState.select_task(row["id"]),
            class_name=rx.cond(
                UIState.selected_task_id == row["id"],
                "dc-task-dialog-select-btn dc-task-dialog-select-btn-active",
                "dc-task-dialog-select-btn",
            ),
        ),
        rx.dropdown_menu.root(
            rx.dropdown_menu.trigger(
                rx.button(
                    rx.icon(tag="ellipsis", class_name="dc-dialog-action-icon"),
                    class_name="dc-dialog-action-trigger",
                )
            ),
            rx.dropdown_menu.content(
                rx.dropdown_menu.item(
                    rx.hstack(
                        rx.icon(tag="square_pen", class_name="dc-nav-icon"),
                        rx.text("重命名"),
                        spacing="2",
                    ),
                    on_select=lambda: UIState.start_rename_task(row["id"], row["task"]),
                    class_name="dc-dialog-menu-item",
                ),
                rx.dropdown_menu.item(
                    rx.hstack(
                        rx.icon(
                            tag=rx.cond(row["is_pinned"] == "1", "pin_off", "pin"),
                            class_name="dc-nav-icon",
                        ),
                        rx.text(rx.cond(row["is_pinned"] == "1", "取消置顶", "置顶")),
                        spacing="2",
                    ),
                    on_select=lambda: UIState.toggle_pin_task(row["id"]),
                    class_name="dc-dialog-menu-item",
                ),
                rx.dropdown_menu.separator(class_name="dc-dialog-menu-separator"),
                rx.dropdown_menu.item(
                    rx.hstack(
                        rx.icon(tag="trash_2", class_name="dc-nav-icon"),
                        rx.text("删除"),
                        spacing="2",
                    ),
                    on_select=lambda: UIState.request_delete_task(row["id"]),
                    class_name="dc-dialog-menu-item dc-dialog-menu-item-danger",
                ),
                class_name="dc-dialog-menu-content",
                side="bottom",
                align="end",
                side_offset=6,
            ),
            class_name="dc-dialog-action-root",
        ),
        class_name=rx.cond(
            UIState.selected_task_id == row["id"],
            "dc-task-dialog-item dc-task-dialog-item-selected",
            "dc-task-dialog-item",
        ),
    )


def _task_dialog_group(group: TaskDialogGroup) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.button(
                rx.hstack(
                    rx.icon(
                        tag=rx.cond(group["collapsed"] == "1", "chevron_down", "chevron_up"),
                        class_name="dc-nav-icon",
                    ),
                    rx.text(group["title"], class_name="dc-task-group-title"),
                    rx.text(f"{group['count']} 项", class_name="dc-task-group-count"),
                    spacing="2",
                    align="center",
                ),
                on_click=lambda: UIState.toggle_task_group_fold(group["key"]),
                class_name="dc-task-group-toggle",
            ),
            rx.button(
                rx.hstack(
                    rx.icon(tag="trash_2", class_name="dc-nav-icon"),
                    rx.text("删除本组"),
                    spacing="2",
                    align="center",
                ),
                on_click=lambda: UIState.request_delete_task_group(group["key"]),
                class_name="dc-task-group-delete",
            ),
            justify="between",
            align="center",
            width="100%",
            class_name="dc-task-group-header",
        ),
        rx.cond(
            group["collapsed"] == "1",
            rx.fragment(),
            rx.vstack(
                rx.foreach(group["items"], _task_dialog_item),
                spacing="2",
                width="100%",
            ),
        ),
        class_name="dc-task-group",
    )


def _task_delete_modal() -> rx.Component:
    return rx.cond(
        (UIState.task_delete_confirm_id != "") | (UIState.task_delete_confirm_group_key != ""),
        rx.box(
            rx.box(class_name="dc-session-modal-backdrop", on_click=UIState.cancel_delete_task),
            rx.box(
                rx.vstack(
                    rx.cond(
                        UIState.task_delete_confirm_group_key != "",
                        rx.text("永久删除该日期分组", class_name="dc-session-modal-title"),
                        rx.text("永久删除任务", class_name="dc-session-modal-title"),
                    ),
                    rx.cond(
                        UIState.task_delete_confirm_group_key != "",
                        rx.text(
                            f"将删除“{UIState.task_delete_confirm_group_title}”分组下的 {UIState.task_delete_confirm_group_count} 条任务记录与工件，且不可恢复。确认继续吗？",
                            class_name="dc-session-modal-warning",
                        ),
                        rx.text("删除后，该任务记录与工件将不可恢复。确认删除吗？", class_name="dc-session-modal-warning"),
                    ),
                    rx.hstack(
                        rx.button(
                            rx.cond(UIState.task_delete_confirm_group_key != "", "删除本组", "确认"),
                            on_click=UIState.delete_requested_task,
                            class_name="dc-small-btn dc-session-modal-btn dc-session-modal-btn-danger",
                        ),
                        rx.button(
                            "取消",
                            on_click=UIState.cancel_delete_task,
                            class_name="dc-small-btn dc-session-modal-btn",
                        ),
                        spacing="3",
                        width="100%",
                        justify="end",
                        class_name="dc-session-modal-actions",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                class_name="dc-session-modal-card",
            ),
            class_name="dc-session-modal-layer",
        ),
        rx.fragment(),
    )


def _task_rename_modal() -> rx.Component:
    return rx.cond(
        UIState.task_rename_id != "",
        rx.box(
            rx.box(class_name="dc-session-modal-backdrop", on_click=UIState.cancel_task_rename),
            rx.box(
                rx.vstack(
                    rx.text("修改任务标题", class_name="dc-session-modal-title"),
                    rx.input(
                        value=UIState.task_rename_value,
                        on_change=UIState.set_task_rename_value,
                        placeholder="输入任务标题",
                        class_name="dc-input dc-session-modal-input",
                    ),
                    rx.hstack(
                        rx.button(
                            "保存",
                            on_click=UIState.save_task_rename,
                            class_name="dc-small-btn dc-session-modal-btn dc-session-modal-btn-primary",
                        ),
                        rx.button(
                            "取消",
                            on_click=UIState.cancel_task_rename,
                            class_name="dc-small-btn dc-session-modal-btn",
                        ),
                        spacing="3",
                        width="100%",
                        justify="end",
                        class_name="dc-session-modal-actions",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                class_name="dc-session-modal-card",
            ),
            class_name="dc-session-modal-layer",
        ),
        rx.fragment(),
    )


def _task_runtime_step_card(step: dict[str, str]) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(step["title"], class_name="dc-task-runtime-step-title"),
            rx.hstack(
                _status_badge(step["status"]),
                rx.button(
                    rx.icon(
                        tag=rx.cond(step["collapsed"] == "1", "chevron_down", "chevron_up"),
                        class_name="dc-nav-icon",
                    ),
                    on_click=lambda: UIState.toggle_task_step_fold(step["id"]),
                    class_name="dc-task-runtime-step-toggle",
                ),
                spacing="2",
                align="center",
            ),
            width="100%",
            justify="between",
            align="center",
        ),
        rx.cond(
            step["collapsed"] == "1",
            rx.fragment(),
            rx.text(step["detail"], class_name="dc-task-runtime-step-detail", white_space="pre-wrap"),
        ),
        class_name="dc-task-runtime-step-card",
    )


def _chat_dialog_group(group: ChatSessionGroup) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.button(
                rx.hstack(
                    rx.icon(
                        tag=rx.cond(group["collapsed"] == "1", "chevron_down", "chevron_up"),
                        class_name="dc-nav-icon",
                    ),
                    rx.text(group["title"], class_name="dc-task-group-title"),
                    rx.text(f"{group['count']} 项", class_name="dc-task-group-count dc-chat-group-count-badge"),
                    spacing="2",
                    align="center",
                ),
                on_click=lambda: UIState.toggle_session_group_fold(group["key"]),
                class_name="dc-task-group-toggle dc-chat-group-toggle",
            ),
            rx.button(
                rx.hstack(
                    rx.icon(tag="trash_2", class_name="dc-nav-icon"),
                    rx.text("删除本组"),
                    spacing="2",
                    align="center",
                ),
                on_click=lambda: UIState.request_delete_session_group(group["key"]),
                class_name="dc-task-group-delete dc-chat-group-delete",
            ),
            justify="between",
            align="center",
            width="100%",
            class_name="dc-task-group-header dc-chat-group-header",
        ),
        rx.cond(
            group["collapsed"] == "1",
            rx.fragment(),
            rx.vstack(
                rx.foreach(group["items"], _chat_dialog_item),
                spacing="2",
                width="100%",
                class_name="dc-chat-group-items",
            ),
        ),
        class_name=rx.cond(
            group["collapsed"] == "1",
            "dc-chat-group dc-task-group dc-chat-group-collapsed",
            "dc-chat-group dc-task-group",
        ),
    )


def _chat_message_card(msg: dict[str, str]) -> rx.Component:
    return rx.box(
        rx.cond(
            msg["role"] == "user",
            rx.cond(
                UIState.chat_edit_message_id == msg["id"],
                rx.box(
                    rx.text_area(
                        value=UIState.chat_edit_prompt,
                        on_change=UIState.set_chat_edit_prompt,
                        rows="1",
                        id="dc-chat-edit-input",
                        class_name="dc-message-edit-input",
                    ),
                    rx.hstack(
                        rx.button(
                            "取消",
                            on_click=UIState.cancel_edit_message,
                            id="dc-chat-edit-cancel-btn",
                            class_name="dc-message-edit-cancel-btn",
                        ),
                        rx.button(
                            rx.icon(tag="send", class_name="dc-chat-action-icon"),
                            "发送",
                            on_click=UIState.resend_edited_message,
                            id="dc-chat-edit-send-btn",
                            class_name="dc-message-edit-send-btn",
                            disabled=UIState.busy,
                        ),
                        justify="end",
                        width="100%",
                        spacing="2",
                        class_name="dc-message-edit-actions",
                    ),
                    class_name="dc-message-editor-card",
                ),
                rx.box(
                    rx.hstack(
                        rx.text(msg["created_at"], class_name="dc-muted"),
                        rx.cond(
                            (msg["status"] == "pending") | (msg["status"] == "streaming"),
                            rx.box(class_name="dc-message-status-dot"),
                            rx.cond(
                                msg["status"] == "error",
                                rx.text("响应失败", class_name="dc-message-status dc-message-status-error"),
                                rx.fragment(),
                            ),
                        ),
                        spacing="2",
                        wrap="wrap",
                    ),
                    rx.markdown(msg["content"], class_name="dc-message-markdown"),
                    rx.box(msg["content"], class_name="dc-message-copy-source"),
                    class_name="dc-message dc-message-user",
                ),
            ),
            rx.box(
                rx.hstack(
                    rx.text(msg["created_at"], class_name="dc-muted"),
                    rx.cond(
                        (msg["status"] == "pending") | (msg["status"] == "streaming"),
                        rx.box(class_name="dc-message-status-dot"),
                        rx.cond(
                            msg["status"] == "error",
                            rx.text("响应失败", class_name="dc-message-status dc-message-status-error"),
                            rx.fragment(),
                        ),
                    ),
                    spacing="2",
                    wrap="wrap",
                ),
                rx.cond(
                    (msg["trace_intent"] != "")
                    | (msg["trace_plan"] != "")
                    | (msg["trace_skills"] != "")
                    | (msg["trace_mcp"] != ""),
                    rx.box(
                        rx.button(
                            rx.hstack(
                                rx.text("Agent Context", class_name="dc-agent-context-title"),
                                rx.icon(
                                    tag=rx.cond(msg["trace_context_collapsed"] == "1", "chevron_down", "chevron_up"),
                                    class_name="dc-agent-context-chevron",
                                ),
                                spacing="2",
                                align="center",
                                width="100%",
                            ),
                            on_click=lambda: UIState.toggle_chat_context(msg["id"]),
                            class_name="dc-agent-context-toggle",
                        ),
                        rx.cond(
                            msg["trace_context_collapsed"] == "1",
                            rx.fragment(),
                            rx.vstack(
                                rx.cond(
                                    msg["trace_intent"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("Intent Route", class_name="dc-agent-context-tag"),
                                        rx.text(msg["trace_intent"], class_name="dc-agent-context-body", white_space="pre-wrap"),
                                        class_name="dc-agent-context-section",
                                    ),
                                ),
                                rx.cond(
                                    msg["trace_plan"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("Plan", class_name="dc-agent-context-tag"),
                                        rx.text(msg["trace_plan"], class_name="dc-agent-context-body", white_space="pre-wrap"),
                                        class_name="dc-agent-context-section",
                                    ),
                                ),
                                rx.cond(
                                    msg["trace_skills"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("Skills", class_name="dc-agent-context-tag"),
                                        rx.text(msg["trace_skills"], class_name="dc-agent-context-body", white_space="pre-wrap"),
                                        class_name="dc-agent-context-section",
                                    ),
                                ),
                                rx.cond(
                                    msg["trace_mcp"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("MCP", class_name="dc-agent-context-tag"),
                                        rx.text(msg["trace_mcp"], class_name="dc-agent-context-body", white_space="pre-wrap"),
                                        class_name="dc-agent-context-section",
                                    ),
                                ),
                                spacing="2",
                                align="stretch",
                            ),
                        ),
                        class_name="dc-agent-context-card",
                    ),
                    rx.fragment(),
                ),
                rx.cond(
                    (msg["trace_reason"] != "")
                    | (msg["trace_function_call"] != "")
                    | (msg["trace_observation"] != ""),
                    rx.box(
                        rx.button(
                            rx.hstack(
                                rx.icon(tag="activity", class_name="dc-agent-trace-icon"),
                                rx.text(
                                    rx.cond(
                                        msg["trace_elapsed"] != "",
                                        msg["trace_elapsed"],
                                        "已思考",
                                    ),
                                    class_name="dc-agent-trace-title",
                                ),
                                rx.icon(
                                    tag=rx.cond(msg["trace_collapsed"] == "1", "chevron_down", "chevron_up"),
                                    class_name="dc-agent-trace-chevron",
                                ),
                                spacing="2",
                                align="center",
                            ),
                            on_click=lambda: UIState.toggle_chat_trace(msg["id"]),
                            class_name="dc-agent-trace-toggle",
                        ),
                        rx.cond(
                            msg["trace_collapsed"] == "1",
                            rx.fragment(),
                            rx.vstack(
                                rx.cond(
                                    msg["trace_reason"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("Reason", class_name="dc-agent-trace-tag dc-agent-trace-tag-reason"),
                                        rx.text(msg["trace_reason"], class_name="dc-agent-trace-body", white_space="pre-wrap"),
                                        class_name="dc-agent-trace-section",
                                    ),
                                ),
                                rx.cond(
                                    msg["trace_function_call"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("Function Call", class_name="dc-agent-trace-tag dc-agent-trace-tag-call"),
                                        rx.text(msg["trace_function_call"], class_name="dc-agent-trace-body", white_space="pre-wrap"),
                                        class_name="dc-agent-trace-section",
                                    ),
                                ),
                                rx.cond(
                                    msg["trace_observation"] == "",
                                    rx.fragment(),
                                    rx.box(
                                        rx.text("Observation", class_name="dc-agent-trace-tag dc-agent-trace-tag-observation"),
                                        rx.text(msg["trace_observation"], class_name="dc-agent-trace-body", white_space="pre-wrap"),
                                        class_name="dc-agent-trace-section",
                                    ),
                                ),
                                spacing="2",
                                align="stretch",
                            ),
                        ),
                        class_name="dc-agent-trace-box",
                    ),
                    rx.fragment(),
                ),
                rx.markdown(msg["content"], class_name="dc-message-markdown"),
                rx.box(msg["content"], class_name="dc-message-copy-source"),
                class_name=rx.cond(
                    msg["status"] == "error",
                    "dc-message dc-message-assistant dc-message-error-state",
                    rx.cond(
                        msg["status"] == "done",
                        "dc-message dc-message-assistant",
                        "dc-message dc-message-assistant dc-message-thinking",
                    ),
                ),
            ),
        ),
        rx.cond(
            msg["role"] == "user",
            rx.cond(
                UIState.chat_edit_message_id == msg["id"],
                rx.fragment(),
                rx.hstack(
                    rx.button(
                        rx.icon(tag="copy", class_name="dc-chat-action-icon"),
                        rx.box(msg["content"], class_name="dc-message-copy-source"),
                        on_click=lambda: [rx.set_clipboard(msg["content"]), UIState.notify_copy_success()],
                        class_name="dc-message-tool-btn dc-message-copy-btn",
                    ),
                    rx.button(
                        rx.icon(tag="square_pen", class_name="dc-chat-action-icon"),
                        on_click=lambda: UIState.start_edit_user_message(msg["id"]),
                        class_name="dc-message-tool-btn dc-message-edit-btn",
                    ),
                    spacing="2",
                    class_name="dc-message-tools dc-message-tools-user",
                ),
            ),
            rx.hstack(
                rx.button(
                    rx.icon(tag="copy", class_name="dc-chat-action-icon"),
                    rx.box(msg["content"], class_name="dc-message-copy-source"),
                    on_click=lambda: [rx.set_clipboard(msg["content"]), UIState.notify_copy_success()],
                    class_name="dc-message-tool-btn dc-message-copy-btn",
                ),
                spacing="2",
                class_name="dc-message-tools dc-message-tools-assistant",
            ),
        ),
        class_name=rx.cond(
            msg["role"] == "user",
            "dc-message-row dc-message-row-user",
            "dc-message-row dc-message-row-assistant",
        ),
    )


def _chat_interaction_script() -> rx.Component:
    return rx.script(
        """
(() => {
    const resolveTextarea = (id) => {
        const node = document.getElementById(id);
        if (!node) {
            return null;
        }
        if (node instanceof HTMLTextAreaElement) {
            return node;
        }
        const nested = node.querySelector("textarea");
        return nested instanceof HTMLTextAreaElement ? nested : null;
    };

    const resolveButton = (id) => {
        const node = document.getElementById(id);
        if (!node) {
            return null;
        }
        if (node instanceof HTMLButtonElement) {
            return node;
        }
        const nested = node.querySelector("button");
        return nested instanceof HTMLButtonElement ? nested : null;
    };

    const scrollBox = document.getElementById("dc-chat-message-scroll");
    const modeSelect = document.getElementById("dc-chat-mode-select");
    const planSelect = document.getElementById("dc-chat-plan-select");
    const maxEditInputHeight = 264;
    const modeStorageKey = "deepcode.chat.mode";
    const planStorageKey = "deepcode.chat.plan_only";

    const bindStoredSelect = (element, storageKey, allowedValues) => {
        if (!(element instanceof HTMLSelectElement)) {
            return;
        }
        if (!element.dataset.dcPersistBound) {
            element.dataset.dcPersistBound = "1";
            element.addEventListener("change", () => {
                window.localStorage.setItem(storageKey, element.value);
            });
        }
        let savedValue = window.localStorage.getItem(storageKey);
        if (storageKey === planStorageKey) {
            if (savedValue === "enabled") {
                savedValue = "仅输出计划";
            } else if (savedValue === "disabled") {
                savedValue = "完整回复";
            }
        }
        if (!savedValue || !allowedValues.includes(savedValue) || element.value === savedValue) {
            return;
        }
        element.value = savedValue;
        element.dispatchEvent(new Event("change", { bubbles: true }));
    };

    const bindEditInput = () => {
        const editInput = resolveTextarea("dc-chat-edit-input");
        if (!(editInput instanceof HTMLTextAreaElement) || editInput.dataset.dcEditBound) {
            return;
        }
        editInput.dataset.dcEditBound = "1";

        const resizeEditInput = () => {
            editInput.style.height = "0px";
            const nextHeight = Math.min(editInput.scrollHeight, maxEditInputHeight);
            editInput.style.height = `${Math.max(nextHeight, 44)}px`;
            editInput.style.overflowY = editInput.scrollHeight > maxEditInputHeight ? "auto" : "hidden";
        };

        resizeEditInput();
        editInput.addEventListener("input", resizeEditInput);
        editInput.addEventListener("keydown", (event) => {
            if (event.isComposing) {
                return;
            }
            const shouldSend = event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
            if (!shouldSend) {
                return;
            }
            const editSendBtn = resolveButton("dc-chat-edit-send-btn");
            if (!editSendBtn || editSendBtn.disabled) {
                return;
            }
            event.preventDefault();
            editSendBtn.click();
        });

        requestAnimationFrame(() => {
            editInput.focus();
            editInput.setSelectionRange(editInput.value.length, editInput.value.length);
        });
    };

    const bindPromptInput = () => {
        const input = resolveTextarea("dc-chat-prompt");
        if (!(input instanceof HTMLTextAreaElement) || input.dataset.dcEnterBound) {
            return;
        }
        input.dataset.dcEnterBound = "1";
        input.addEventListener("keydown", (event) => {
            if (event.isComposing) {
                return;
            }
            const shouldSend = event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
            if (!shouldSend) {
                return;
            }
            const currentBtn = resolveButton("dc-chat-send-btn");
            if (!currentBtn || currentBtn.disabled || currentBtn.classList.contains("dc-chat-stop-btn")) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            currentBtn.click();
        });
    };

    bindPromptInput();
    bindStoredSelect(modeSelect, modeStorageKey, ["ask", "agent"]);
    bindStoredSelect(planSelect, planStorageKey, ["完整回复", "仅输出计划", "disabled", "enabled"]);

    if (window.__dcPromptEnterHandler) {
        document.removeEventListener("keydown", window.__dcPromptEnterHandler, true);
    }
    window.__dcPromptEnterHandler = (event) => {
        if (event.defaultPrevented || event.isComposing) {
            return;
        }
        const shouldSend = event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
        if (!shouldSend) {
            return;
        }

        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const inPrompt = target.id === "dc-chat-prompt" || Boolean(target.closest("#dc-chat-prompt"));
        if (!inPrompt) {
            return;
        }

        const promptInput = resolveTextarea("dc-chat-prompt");
        if (!(promptInput instanceof HTMLTextAreaElement)) {
            return;
        }
        if (target !== promptInput) {
            return;
        }

        const currentBtn = resolveButton("dc-chat-send-btn");
        if (!currentBtn || currentBtn.disabled || currentBtn.classList.contains("dc-chat-stop-btn")) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();
        currentBtn.click();
    };
    document.addEventListener("keydown", window.__dcPromptEnterHandler, true);

    if (window.__dcMessageActionHandler) {
        document.removeEventListener("click", window.__dcMessageActionHandler, true);
    }
    window.__dcMessageActionHandler = (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        const editBtn = target.closest(".dc-message-edit-btn");
        if (editBtn) {
            setTimeout(bindEditInput, 120);
            return;
        }
        const editorCard = document.querySelector(".dc-message-editor-card");
        if (editorCard && !target.closest(".dc-message-editor-card")) {
            const cancelBtn = document.getElementById("dc-chat-edit-cancel-btn");
            if (cancelBtn && !cancelBtn.disabled) {
                cancelBtn.click();
            }
        }
    };
    document.addEventListener("click", window.__dcMessageActionHandler, true);

    bindEditInput();

    if (scrollBox && !scrollBox.dataset.dcScrollBound) {
        if (window.__dcChatScrollObserver) {
            window.__dcChatScrollObserver.disconnect();
        }
        if (window.__dcChatScrollRaf) {
            cancelAnimationFrame(window.__dcChatScrollRaf);
            window.__dcChatScrollRaf = null;
        }
        scrollBox.dataset.dcScrollBound = "1";
        scrollBox.dataset.dcStickBottom = "1";

        const scrollToBottom = () => {
            scrollBox.scrollTop = scrollBox.scrollHeight;
        };

        const updateStickBottom = () => {
            const distance = scrollBox.scrollHeight - scrollBox.scrollTop - scrollBox.clientHeight;
            scrollBox.dataset.dcStickBottom = distance <= 48 ? "1" : "0";
        };

        scrollToBottom();
        scrollBox.addEventListener("scroll", updateStickBottom, { passive: true });

        let lastHeight = scrollBox.scrollHeight;

        const observer = new MutationObserver(() => {
            bindEditInput();
            bindPromptInput();
            if (scrollBox.dataset.dcStickBottom !== "0") {
                requestAnimationFrame(scrollToBottom);
            }
            lastHeight = scrollBox.scrollHeight;
        });
        observer.observe(scrollBox, { childList: true, subtree: true, characterData: true });
        window.__dcChatScrollObserver = observer;

        const watchHeight = () => {
            if (!scrollBox.isConnected) {
                return;
            }
            const nextHeight = scrollBox.scrollHeight;
            if (nextHeight !== lastHeight) {
                if (scrollBox.dataset.dcStickBottom !== "0") {
                    scrollToBottom();
                }
                lastHeight = nextHeight;
            }
            window.__dcChatScrollRaf = requestAnimationFrame(watchHeight);
        };
        window.__dcChatScrollRaf = requestAnimationFrame(watchHeight);
    }
})();
        """
    )


def _task_interaction_script() -> rx.Component:
    return rx.script(
        """
(() => {
    const input = document.getElementById("dc-task-prompt");
    if (!input || input.dataset.dcEnterBound) {
        return;
    }
    input.dataset.dcEnterBound = "1";
    input.addEventListener("keydown", (event) => {
        if (event.isComposing) {
            return;
        }
        const shouldSend = event.key === "Enter" && !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey;
        if (!shouldSend) {
            return;
        }
        const sendBtn = document.getElementById("dc-task-send-btn");
        if (!sendBtn || !sendBtn.classList.contains("dc-chat-send-btn-ready") || sendBtn.disabled) {
            return;
        }
        event.preventDefault();
        sendBtn.click();
    });
})();
        """
    )


def chat_page() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.box(
                rx.hstack(
                    rx.text("对话列表", class_name="dc-card-title"),
                    rx.button(
                        rx.hstack(rx.icon(tag="plus", class_name="dc-nav-icon"), rx.text("新建对话"), spacing="2"),
                        on_click=UIState.create_session,
                        class_name="dc-small-btn",
                    ),
                    justify="between",
                    align="center",
                    width="100%",
                ),
                rx.cond(
                    UIState.chat_session_groups.length() > 0,
                    rx.box(rx.foreach(UIState.chat_session_groups, _chat_dialog_group), class_name="dc-chat-session-scroll dc-scroll-host"),
                    rx.box(rx.text("暂无对话", class_name="dc-muted"), class_name="dc-chat-session-scroll dc-scroll-host"),
                ),
                class_name="dc-card dc-chat-panel dc-chat-list-panel",
            ),
            rx.box(
                rx.hstack(
                    rx.text("消息流", class_name="dc-card-title"),
                    rx.hstack(
                        rx.vstack(
                            rx.text("对话模式", class_name="dc-field-label"),
                            rx.select(
                                ["ask", "agent"],
                                value=UIState.chat_mode,
                                on_change=UIState.set_chat_mode,
                                id="dc-chat-mode-select",
                                class_name="dc-chat-mode-select",
                            ),
                            spacing="1",
                            align="start",
                            class_name="dc-chat-control-group",
                        ),
                        rx.vstack(
                            rx.text("仅规划", class_name="dc-field-label"),
                            rx.select(
                                ["完整回复", "仅输出计划"],
                                value=UIState.chat_plan_only_label,
                                on_change=UIState.set_chat_plan_only,
                                id="dc-chat-plan-select",
                                class_name="dc-chat-plan-select",
                            ),
                            spacing="1",
                            align="start",
                            class_name="dc-chat-control-group",
                        ),
                        rx.cond(
                            UIState.model_profiles.length() > 0,
                            rx.vstack(
                                rx.text("模型配置", class_name="dc-field-label"),
                                rx.select(
                                    UIState.model_profile_names,
                                    value=UIState.active_model_profile_name,
                                    on_change=UIState.switch_model_profile_by_name,
                                    class_name="dc-chat-model-select",
                                ),
                                spacing="1",
                                align="start",
                                class_name="dc-chat-control-group",
                            ),
                            rx.fragment(),
                        ),
                        rx.vstack(
                            rx.text(
                                rx.cond(UIState.ui_language == "en", "Context Length", "上下文长度"),
                                class_name="dc-field-label",
                            ),
                            rx.text(UIState.chat_context_token_label, class_name="dc-chat-token-badge"),
                            spacing="1",
                            align="start",
                            class_name="dc-chat-control-group",
                        ),
                        spacing="2",
                        align="end",
                    ),
                    justify="between",
                    align="end",
                    width="100%",
                ),
                rx.box(
                    rx.cond(
                        UIState.chat_messages.length() > 0,
                        rx.foreach(UIState.chat_messages, _chat_message_card),
                        rx.text("请选择对话或创建新对话", class_name="dc-muted"),
                    ),
                    id="dc-chat-message-scroll",
                    class_name="dc-chat-message-scroll dc-scroll-host",
                ),
                rx.hstack(
                    rx.vstack(
                        rx.text_area(
                            placeholder="描述你要实现、排查或重构的内容...",
                            value=UIState.chat_prompt,
                            on_change=UIState.set_chat_prompt,
                            rows="1",
                            id="dc-chat-prompt",
                            class_name="dc-input dc-chat-input",
                        ),
                        rx.text("Enter 发送 · Shift/Ctrl+Enter 换行", class_name="dc-chat-input-hint"),
                        spacing="1",
                        align="start",
                        width="100%",
                    ),
                    rx.hstack(
                        rx.button(
                            rx.icon(tag="paperclip", class_name="dc-chat-action-icon"),
                            class_name="dc-chat-upload-btn",
                        ),
                        rx.cond(
                            UIState.busy,
                            rx.button(
                                rx.icon(tag="square", class_name="dc-chat-action-icon"),
                                on_click=UIState.stop_chat_generation,
                                id="dc-chat-send-btn",
                                class_name="dc-chat-stop-btn",
                            ),
                            rx.button(
                                rx.icon(tag="send", class_name="dc-chat-action-icon"),
                                on_click=UIState.send_chat,
                                id="dc-chat-send-btn",
                                class_name="dc-cta dc-chat-send-btn dc-chat-send-btn-ready",
                                disabled=UIState.chat_send_disabled,
                            ),
                        ),
                        class_name="dc-chat-input-actions",
                        spacing="2",
                        align="end",
                    ),
                    class_name="dc-chat-input-row",
                    width="100%",
                    align="end",
                    spacing="3",
                ),
                class_name="dc-card dc-chat-panel",
            ),
            class_name="dc-chat-layout",
        ),
        _chat_interaction_script(),
        _session_delete_modal(),
        _session_rename_modal(),
        _skill_delete_modal(),
        width="100%",
        spacing="4",
    )


def task_page() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.box(
                rx.hstack(
                    rx.text("任务列表", class_name="dc-card-title"),
                    rx.button(
                        rx.hstack(rx.icon(tag="plus", class_name="dc-nav-icon"), rx.text("新建任务"), spacing="2"),
                        on_click=UIState.create_empty_task,
                        class_name="dc-small-btn",
                    ),
                    justify="between",
                    align="center",
                    width="100%",
                ),
                rx.box(
                    rx.cond(
                        UIState.task_groups.length() > 0,
                        rx.foreach(UIState.task_groups, _task_dialog_group),
                        rx.text("暂无任务", class_name="dc-muted"),
                    ),
                    class_name="dc-task-list-scroll dc-scroll-host",
                ),
                class_name="dc-card dc-task-panel dc-task-list-panel",
            ),
            rx.box(
                rx.hstack(
                    rx.text("任务详情", class_name="dc-card-title"),
                    rx.hstack(
                        rx.cond(
                            UIState.model_profiles.length() > 0,
                            rx.select(
                                UIState.model_profile_names,
                                value=UIState.active_model_profile_name,
                                on_change=UIState.switch_model_profile_by_name,
                                class_name="dc-chat-model-select",
                            ),
                            rx.fragment(),
                        ),
                        spacing="2",
                        align="center",
                    ),
                    justify="between",
                    align="center",
                    width="100%",
                ),
                rx.cond(
                    UIState.task_busy,
                    rx.hstack(
                        rx.text(UIState.task_runtime_phase, class_name="dc-task-runtime-phase"),
                        rx.text(UIState.task_runtime_cursor, class_name="dc-task-runtime-cursor"),
                        spacing="2",
                        align="center",
                        class_name="dc-task-runtime-cursor-row",
                    ),
                    rx.fragment(),
                ),
                rx.cond(
                    UIState.task_runtime_steps.length() > 0,
                    rx.box(
                        rx.foreach(UIState.task_runtime_steps, _task_runtime_step_card),
                        class_name="dc-task-runtime-step-list",
                    ),
                    rx.fragment(),
                ),
                rx.box(
                    rx.cond(
                        UIState.selected_task_id != "",
                        rx.vstack(
                            rx.text(f"任务 ID: {UIState.selected_task_id}", class_name="dc-id"),
                            rx.cond(
                                (UIState.task_agent_intent != "")
                                | (UIState.task_agent_plan != "")
                                | (UIState.task_agent_skills != "")
                                | (UIState.task_agent_mcp != ""),
                                rx.box(
                                    rx.text("Chat Agent Context", class_name="dc-sub-title"),
                                    rx.box(
                                        rx.cond(
                                            UIState.task_agent_intent == "",
                                            rx.fragment(),
                                            rx.box(
                                                rx.text("Intent Route", class_name="dc-agent-context-tag"),
                                                rx.text(
                                                    UIState.task_agent_intent,
                                                    class_name="dc-agent-context-body",
                                                    white_space="pre-wrap",
                                                ),
                                                class_name="dc-agent-context-section",
                                            ),
                                        ),
                                        rx.cond(
                                            UIState.task_agent_plan == "",
                                            rx.fragment(),
                                            rx.box(
                                                rx.text("Plan", class_name="dc-agent-context-tag"),
                                                rx.text(
                                                    UIState.task_agent_plan,
                                                    class_name="dc-agent-context-body",
                                                    white_space="pre-wrap",
                                                ),
                                                class_name="dc-agent-context-section",
                                            ),
                                        ),
                                        rx.cond(
                                            UIState.task_agent_skills == "",
                                            rx.fragment(),
                                            rx.box(
                                                rx.text("Skills", class_name="dc-agent-context-tag"),
                                                rx.text(
                                                    UIState.task_agent_skills,
                                                    class_name="dc-agent-context-body",
                                                    white_space="pre-wrap",
                                                ),
                                                class_name="dc-agent-context-section",
                                            ),
                                        ),
                                        rx.cond(
                                            UIState.task_agent_mcp == "",
                                            rx.fragment(),
                                            rx.box(
                                                rx.text("MCP", class_name="dc-agent-context-tag"),
                                                rx.text(
                                                    UIState.task_agent_mcp,
                                                    class_name="dc-agent-context-body",
                                                    white_space="pre-wrap",
                                                ),
                                                class_name="dc-agent-context-section",
                                            ),
                                        ),
                                        class_name="dc-agent-context-card",
                                    ),
                                    class_name="dc-sub-card",
                                ),
                                rx.fragment(),
                            ),
                            rx.cond(
                                UIState.task_plan.length() > 0,
                                rx.box(
                                    rx.text("执行计划", class_name="dc-sub-title"),
                                    rx.foreach(
                                        UIState.task_plan,
                                        lambda step: rx.text(f"- {step}"),
                                    ),
                                    class_name="dc-sub-card",
                                ),
                                rx.fragment(),
                            ),
                            rx.cond(
                                UIState.task_artifacts.length() > 0,
                                rx.box(
                                    rx.text("代码工件", class_name="dc-sub-title"),
                                    rx.foreach(
                                        UIState.task_artifacts,
                                        lambda artifact: rx.box(
                                            rx.text(artifact["filename"], class_name="dc-id"),
                                            rx.code_block(
                                                artifact["content"],
                                                language=artifact["language"],
                                                can_copy=True,
                                                class_name="dc-codeblock",
                                            ),
                                            class_name="dc-sub-card",
                                        ),
                                    ),
                                ),
                                rx.fragment(),
                            ),
                            rx.cond(
                                UIState.task_execution_timeline.length() > 0,
                                rx.box(
                                    rx.text("执行时间线", class_name="dc-sub-title"),
                                    rx.box(
                                        rx.foreach(UIState.task_execution_timeline, _task_runtime_step_card),
                                        class_name="dc-task-runtime-step-list",
                                    ),
                                    class_name="dc-sub-card",
                                ),
                                rx.fragment(),
                            ),
                            rx.cond(
                                UIState.task_review_score != "",
                                rx.box(
                                    rx.text("复盘结果", class_name="dc-sub-title"),
                                    rx.text(f"评分: {UIState.task_review_score}/10"),
                                    rx.foreach(
                                        UIState.task_review_issues,
                                        lambda issue: rx.text(f"- {issue}"),
                                    ),
                                    class_name="dc-sub-card",
                                ),
                                rx.fragment(),
                            ),
                            rx.cond(
                                UIState.task_error != "",
                                rx.box(rx.text(UIState.task_error), class_name="dc-error"),
                                rx.fragment(),
                            ),
                            spacing="3",
                            align="start",
                            width="100%",
                        ),
                        rx.text("请选择任务查看详情", class_name="dc-muted"),
                    ),
                    class_name="dc-task-detail-scroll dc-scroll-host",
                ),
                rx.hstack(
                    rx.text_area(
                        placeholder="例如：实现一个带测试与文档的 FastAPI CRUD 接口。",
                        value=UIState.task_prompt,
                        on_change=UIState.set_task_prompt,
                        rows="1",
                        id="dc-task-prompt",
                        class_name="dc-input dc-chat-input",
                    ),
                    rx.hstack(
                        rx.cond(
                            UIState.task_busy,
                            rx.button(
                                rx.icon(tag="square", class_name="dc-chat-action-icon"),
                                on_click=UIState.stop_task_generation,
                                id="dc-task-send-btn",
                                class_name="dc-chat-stop-btn",
                            ),
                            rx.button(
                                rx.icon(tag="send", class_name="dc-chat-action-icon"),
                                on_click=UIState.run_task,
                                id="dc-task-send-btn",
                                class_name="dc-cta dc-chat-send-btn dc-chat-send-btn-ready",
                                disabled=UIState.task_send_disabled,
                            ),
                        ),
                        class_name="dc-chat-input-actions",
                        spacing="2",
                        align="end",
                    ),
                    class_name="dc-chat-input-row",
                    width="100%",
                    align="end",
                    spacing="3",
                ),
                class_name="dc-card dc-task-panel dc-task-detail-panel",
            ),
            class_name="dc-task-layout",
        ),
        _task_interaction_script(),
        _task_delete_modal(),
        _task_rename_modal(),
        width="100%",
        spacing="4",
    )


def session_page() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.box(
                rx.text("对话总览", class_name="dc-card-title"),
                rx.cond(
                    UIState.sessions.length() > 0,
                    rx.foreach(
                        UIState.sessions,
                        lambda row: rx.box(
                            rx.hstack(
                                rx.text(row["name"], class_name="dc-row-title"),
                                rx.button("查看", on_click=lambda: UIState.select_session(row["id"]), class_name="dc-small-btn"),
                                justify="between",
                                width="100%",
                            ),
                            rx.text(f"{row['id']} · {row['messages']} 条消息", class_name="dc-muted"),
                            class_name="dc-list-item",
                        ),
                    ),
                    rx.text("暂无对话", class_name="dc-muted"),
                ),
                class_name="dc-card",
            ),
            rx.box(
                rx.text("消息详情", class_name="dc-card-title"),
                rx.cond(
                    UIState.chat_messages.length() > 0,
                    rx.foreach(UIState.chat_messages, _chat_message_card),
                    rx.text("请选择对话", class_name="dc-muted"),
                ),
                class_name="dc-card",
            ),
            class_name="dc-two-col",
        ),
        width="100%",
        spacing="4",
    )


def artifact_page() -> rx.Component:
    return rx.vstack(
        rx.box(
            rx.text("选择任务", class_name="dc-card-title"),
            rx.hstack(
                rx.text(UIState.task_page_label, class_name="dc-muted"),
                rx.button("上一页", on_click=UIState.previous_task_page, class_name="dc-small-btn"),
                rx.button("下一页", on_click=UIState.next_task_page, class_name="dc-small-btn"),
                class_name="dc-pagination-row",
            ),
            rx.cond(
                UIState.paginated_tasks.length() > 0,
                rx.foreach(
                    UIState.paginated_tasks,
                    lambda row: rx.button(
                        f"{row['id'][:8]} · 工件 {row['artifacts']}",
                        on_click=lambda: UIState.select_task(row["id"]),
                        class_name=rx.cond(
                            UIState.selected_task_id == row["id"],
                            "dc-nav-btn dc-nav-btn-active",
                            "dc-nav-btn",
                        ),
                    ),
                ),
                rx.text("暂无任务", class_name="dc-muted"),
            ),
            class_name="dc-card",
        ),
        _card(
            "工件预览",
            rx.cond(
                UIState.task_artifacts.length() > 0,
                rx.foreach(
                    UIState.task_artifacts,
                    lambda artifact: rx.box(
                        rx.text(artifact["filename"], class_name="dc-id"),
                        rx.code_block(
                            artifact["content"],
                            language=artifact["language"],
                            can_copy=True,
                            class_name="dc-codeblock",
                        ),
                        class_name="dc-sub-card",
                    ),
                ),
                rx.text("当前任务没有工件", class_name="dc-muted"),
            ),
        ),
        width="100%",
        spacing="4",
    )


def _bridge_field(
    label_key: str,
    placeholder_key: str,
    value: str,
    on_change: rx.event.EventHandler,
    *,
    input_type: str = "text",
    hint_key: str = "",
) -> rx.Component:
    return rx.vstack(
        rx.text(UIState.i18n[label_key], class_name="dc-field-label"),
        rx.input(
            type=input_type,
            placeholder=UIState.i18n[placeholder_key],
            value=value,
            on_change=on_change,
            class_name="dc-input",
        ),
        rx.cond(
            hint_key != "",
            rx.text(UIState.i18n[hint_key], class_name="dc-form-hint"),
            rx.fragment(),
        ),
        spacing="1",
        width="100%",
        align="start",
        class_name="dc-bridge-field",
    )


def _bridge_select_field(
    label_key: str,
    options: list[str],
    value: str,
    on_change: rx.event.EventHandler,
    *,
    hint_key: str = "",
) -> rx.Component:
    return rx.vstack(
        rx.text(UIState.i18n[label_key], class_name="dc-field-label"),
        rx.select(
            options,
            value=value,
            on_change=on_change,
            class_name="dc-chat-model-select",
        ),
        rx.cond(
            hint_key != "",
            rx.text(UIState.i18n[hint_key], class_name="dc-form-hint"),
            rx.fragment(),
        ),
        spacing="1",
        width="100%",
        align="start",
        class_name="dc-bridge-field",
    )


def platform_bridge_page() -> rx.Component:
    return rx.vstack(
        _card(
            UIState.i18n["platform_bridge.base_card_title"],
            rx.vstack(
                rx.box(
                    rx.text(UIState.i18n["platform_bridge.base_card_hint"], class_name="dc-muted"),
                    class_name="dc-sub-card dc-bridge-banner",
                ),
                rx.hstack(
                    _bridge_select_field(
                        "platform_bridge.base_enabled_label",
                        ["enabled", "disabled"],
                        UIState.platform_bridge_enabled,
                        UIState.set_platform_bridge_enabled,
                    ),
                    _bridge_select_field(
                        "platform_bridge.base_default_mode_label",
                        ["ask", "agent"],
                        UIState.platform_bridge_default_mode,
                        UIState.set_platform_bridge_default_mode,
                    ),
                    _bridge_select_field(
                        "platform_bridge.base_callback_delivery_label",
                        ["enabled", "disabled"],
                        UIState.platform_bridge_callback_delivery_enabled,
                        UIState.set_platform_bridge_callback_delivery_enabled,
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.hstack(
                    _bridge_field(
                        "platform_bridge.base_verify_token_label",
                        "platform_bridge.base_verify_token_placeholder",
                        UIState.platform_bridge_verify_token,
                        UIState.set_platform_bridge_verify_token,
                    ),
                    _bridge_field(
                        "platform_bridge.base_allowed_platforms_label",
                        "platform_bridge.base_allowed_platforms_placeholder",
                        UIState.platform_bridge_allowed_platforms,
                        UIState.set_platform_bridge_allowed_platforms,
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.hstack(
                    _bridge_field(
                        "platform_bridge.base_signature_ttl_label",
                        "platform_bridge.base_signature_ttl_placeholder",
                        UIState.platform_bridge_signature_ttl_seconds,
                        UIState.set_platform_bridge_signature_ttl_seconds,
                        input_type="number",
                    ),
                    _bridge_field(
                        "platform_bridge.base_event_ttl_label",
                        "platform_bridge.base_event_ttl_placeholder",
                        UIState.platform_bridge_event_id_ttl_seconds,
                        UIState.set_platform_bridge_event_id_ttl_seconds,
                        input_type="number",
                    ),
                    _bridge_field(
                        "platform_bridge.base_callback_timeout_label",
                        "platform_bridge.base_callback_timeout_placeholder",
                        UIState.platform_bridge_callback_timeout_seconds,
                        UIState.set_platform_bridge_callback_timeout_seconds,
                        input_type="number",
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.hstack(
                    rx.button(UIState.i18n["platform_bridge.save_button"], on_click=UIState.save_platform_bridge_settings, class_name="dc-cta"),
                    rx.button(UIState.i18n["platform_bridge.reload_button"], on_click=UIState.reload_platform_bridge_settings, class_name="dc-small-btn"),
                    spacing="2",
                    class_name="dc-bridge-actions",
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
            class_name="dc-bridge-card",
        ),
        rx.box(
            _card(
                UIState.i18n["platform_bridge.feishu_card_title"],
                rx.vstack(
                    _bridge_field(
                        "platform_bridge.feishu_encrypt_key_label",
                        "platform_bridge.feishu_encrypt_key_placeholder",
                        UIState.platform_bridge_feishu_encrypt_key,
                        UIState.set_platform_bridge_feishu_encrypt_key,
                        hint_key="platform_bridge.feishu_encrypt_key_hint",
                    ),
                    _bridge_field(
                        "platform_bridge.feishu_api_base_url_label",
                        "platform_bridge.feishu_api_base_url_placeholder",
                        UIState.platform_bridge_feishu_api_base_url,
                        UIState.set_platform_bridge_feishu_api_base_url,
                    ),
                    _bridge_field(
                        "platform_bridge.feishu_app_id_label",
                        "platform_bridge.feishu_app_id_placeholder",
                        UIState.platform_bridge_feishu_app_id,
                        UIState.set_platform_bridge_feishu_app_id,
                    ),
                    _bridge_field(
                        "platform_bridge.feishu_app_secret_label",
                        "platform_bridge.feishu_app_secret_placeholder",
                        UIState.platform_bridge_feishu_app_secret,
                        UIState.set_platform_bridge_feishu_app_secret,
                        input_type="password",
                    ),
                    spacing="2",
                    width="100%",
                    align="start",
                ),
                class_name="dc-bridge-card",
            ),
            _card(
                UIState.i18n["platform_bridge.wechat_card_title"],
                rx.vstack(
                    _bridge_field(
                        "platform_bridge.wechat_token_label",
                        "platform_bridge.wechat_token_placeholder",
                        UIState.platform_bridge_wechat_token,
                        UIState.set_platform_bridge_wechat_token,
                        hint_key="platform_bridge.wechat_token_hint",
                    ),
                    _bridge_select_field(
                        "platform_bridge.wechat_delivery_mode_label",
                        ["auto", "work", "official"],
                        UIState.platform_bridge_wechat_delivery_mode,
                        UIState.set_platform_bridge_wechat_delivery_mode,
                    ),
                    rx.box(
                        rx.text(UIState.i18n["platform_bridge.wechat_work_group_title"], class_name="dc-row-title"),
                        class_name="dc-sub-card",
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_work_api_base_url_label",
                        "platform_bridge.wechat_work_api_base_url_placeholder",
                        UIState.platform_bridge_wechat_work_api_base_url,
                        UIState.set_platform_bridge_wechat_work_api_base_url,
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_work_corp_id_label",
                        "platform_bridge.wechat_work_corp_id_placeholder",
                        UIState.platform_bridge_wechat_work_corp_id,
                        UIState.set_platform_bridge_wechat_work_corp_id,
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_work_corp_secret_label",
                        "platform_bridge.wechat_work_corp_secret_placeholder",
                        UIState.platform_bridge_wechat_work_corp_secret,
                        UIState.set_platform_bridge_wechat_work_corp_secret,
                        input_type="password",
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_work_agent_id_label",
                        "platform_bridge.wechat_work_agent_id_placeholder",
                        UIState.platform_bridge_wechat_work_agent_id,
                        UIState.set_platform_bridge_wechat_work_agent_id,
                    ),
                    rx.box(
                        rx.text(UIState.i18n["platform_bridge.wechat_official_group_title"], class_name="dc-row-title"),
                        class_name="dc-sub-card",
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_official_api_base_url_label",
                        "platform_bridge.wechat_official_api_base_url_placeholder",
                        UIState.platform_bridge_wechat_official_api_base_url,
                        UIState.set_platform_bridge_wechat_official_api_base_url,
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_official_app_id_label",
                        "platform_bridge.wechat_official_app_id_placeholder",
                        UIState.platform_bridge_wechat_official_app_id,
                        UIState.set_platform_bridge_wechat_official_app_id,
                    ),
                    _bridge_field(
                        "platform_bridge.wechat_official_app_secret_label",
                        "platform_bridge.wechat_official_app_secret_placeholder",
                        UIState.platform_bridge_wechat_official_app_secret,
                        UIState.set_platform_bridge_wechat_official_app_secret,
                        input_type="password",
                    ),
                    spacing="2",
                    width="100%",
                    align="start",
                ),
                class_name="dc-bridge-card",
            ),
            class_name="dc-two-col",
        ),
        _card(
            UIState.i18n["platform_bridge.qq_card_title"],
            rx.vstack(
                _bridge_select_field(
                    "platform_bridge.qq_delivery_mode_label",
                    ["auto", "official", "napcat"],
                    UIState.platform_bridge_qq_delivery_mode,
                    UIState.set_platform_bridge_qq_delivery_mode,
                ),
                rx.box(
                    rx.text(UIState.i18n["platform_bridge.qq_official_group_title"], class_name="dc-row-title"),
                    class_name="dc-sub-card",
                ),
                _bridge_field(
                    "platform_bridge.qq_signing_secret_label",
                    "platform_bridge.qq_signing_secret_placeholder",
                    UIState.platform_bridge_qq_signing_secret,
                    UIState.set_platform_bridge_qq_signing_secret,
                    input_type="password",
                ),
                _bridge_field(
                    "platform_bridge.qq_api_base_url_label",
                    "platform_bridge.qq_api_base_url_placeholder",
                    UIState.platform_bridge_qq_api_base_url,
                    UIState.set_platform_bridge_qq_api_base_url,
                ),
                _bridge_field(
                    "platform_bridge.qq_bot_app_id_label",
                    "platform_bridge.qq_bot_app_id_placeholder",
                    UIState.platform_bridge_qq_bot_app_id,
                    UIState.set_platform_bridge_qq_bot_app_id,
                ),
                _bridge_field(
                    "platform_bridge.qq_bot_token_label",
                    "platform_bridge.qq_bot_token_placeholder",
                    UIState.platform_bridge_qq_bot_token,
                    UIState.set_platform_bridge_qq_bot_token,
                    input_type="password",
                ),
                rx.box(
                    rx.text(UIState.i18n["platform_bridge.qq_napcat_group_title"], class_name="dc-row-title"),
                    class_name="dc-sub-card",
                ),
                _bridge_field(
                    "platform_bridge.qq_napcat_api_base_url_label",
                    "platform_bridge.qq_napcat_api_base_url_placeholder",
                    UIState.platform_bridge_qq_napcat_api_base_url,
                    UIState.set_platform_bridge_qq_napcat_api_base_url,
                ),
                _bridge_field(
                    "platform_bridge.qq_napcat_access_token_label",
                    "platform_bridge.qq_napcat_access_token_placeholder",
                    UIState.platform_bridge_qq_napcat_access_token,
                    UIState.set_platform_bridge_qq_napcat_access_token,
                    input_type="password",
                ),
                _bridge_field(
                    "platform_bridge.qq_napcat_webhook_token_label",
                    "platform_bridge.qq_napcat_webhook_token_placeholder",
                    UIState.platform_bridge_qq_napcat_webhook_token,
                    UIState.set_platform_bridge_qq_napcat_webhook_token,
                    input_type="password",
                    hint_key="platform_bridge.qq_napcat_webhook_token_hint",
                ),
                rx.box(
                    rx.text(UIState.i18n["platform_bridge.qq_inbound_group_title"], class_name="dc-row-title"),
                    class_name="dc-sub-card",
                ),
                rx.hstack(
                    _bridge_select_field(
                        "platform_bridge.qq_inbound_enabled_label",
                        ["enabled", "disabled"],
                        UIState.platform_bridge_inbound_enabled,
                        UIState.set_platform_bridge_inbound_enabled,
                    ),
                    _bridge_field(
                        "platform_bridge.qq_inbound_port_label",
                        "platform_bridge.qq_inbound_port_placeholder",
                        UIState.platform_bridge_inbound_port,
                        UIState.set_platform_bridge_inbound_port,
                        input_type="number",
                    ),
                    _bridge_select_field(
                        "platform_bridge.qq_inbound_debug_label",
                        ["enabled", "disabled"],
                        UIState.platform_bridge_inbound_debug,
                        UIState.set_platform_bridge_inbound_debug,
                        hint_key="platform_bridge.qq_inbound_debug_hint",
                    ),
                    spacing="2",
                    width="100%",
                ),
                rx.box(
                    rx.text(UIState.i18n["platform_bridge.qq_inbound_url_label"], class_name="dc-field-label"),
                    rx.text(UIState.platform_bridge_inbound_callback_url, class_name="dc-code", white_space="pre-wrap"),
                    rx.text(UIState.i18n["platform_bridge.qq_inbound_restart_hint"], class_name="dc-form-hint"),
                    spacing="1",
                    width="100%",
                    align="start",
                    class_name="dc-sub-card",
                ),
                rx.box(
                    rx.hstack(
                        rx.text(UIState.i18n["platform_bridge.qq_inbound_listener_title"], class_name="dc-row-title"),
                        rx.spacer(),
                        rx.button(
                            UIState.i18n["platform_bridge.qq_inbound_listener_refresh_status"],
                            on_click=UIState.refresh_platform_bridge_inbound_listener_status,
                            class_name="dc-small-btn",
                        ),
                        rx.button(
                            UIState.i18n["platform_bridge.qq_inbound_listener_start_button"],
                            on_click=UIState.start_platform_bridge_inbound_listener,
                            class_name="dc-small-btn",
                        ),
                        rx.button(
                            UIState.i18n["platform_bridge.qq_inbound_listener_stop_button"],
                            on_click=UIState.stop_platform_bridge_inbound_listener,
                            class_name="dc-small-btn",
                        ),
                        width="100%",
                        align="center",
                    ),
                    rx.text(
                        UIState.i18n["platform_bridge.qq_inbound_listener_status_label"]
                        + ": "
                        + UIState.platform_bridge_inbound_listener_status,
                        class_name="dc-muted",
                    ),
                    rx.text(
                        UIState.i18n["platform_bridge.qq_inbound_listener_pid_label"]
                        + ": "
                        + rx.cond(UIState.platform_bridge_inbound_listener_pid == "", "-", UIState.platform_bridge_inbound_listener_pid),
                        class_name="dc-muted",
                    ),
                    rx.text(
                        UIState.i18n["platform_bridge.qq_inbound_listener_updated_label"]
                        + ": "
                        + rx.cond(
                            UIState.platform_bridge_inbound_listener_updated_at == "",
                            "-",
                            UIState.platform_bridge_inbound_listener_updated_at,
                        ),
                        class_name="dc-muted",
                    ),
                    rx.text(
                        UIState.i18n["platform_bridge.qq_inbound_listener_cli_hint"],
                        class_name="dc-form-hint",
                    ),
                    spacing="2",
                    width="100%",
                    align="start",
                    class_name="dc-sub-card",
                ),
                rx.box(
                    rx.hstack(
                        rx.text(UIState.i18n["platform_bridge.qq_inbound_logs_title"], class_name="dc-row-title"),
                        rx.spacer(),
                        rx.button(
                            UIState.i18n["platform_bridge.qq_inbound_refresh_logs"],
                            on_click=UIState.refresh_platform_bridge_inbound_logs,
                            class_name="dc-small-btn",
                        ),
                        rx.button(
                            UIState.i18n["platform_bridge.qq_inbound_clear_logs"],
                            on_click=UIState.clear_platform_bridge_inbound_logs,
                            class_name="dc-small-btn",
                        ),
                        width="100%",
                        align="center",
                    ),
                    rx.cond(
                        UIState.platform_bridge_inbound_debug == "enabled",
                        rx.cond(
                            UIState.platform_bridge_inbound_logs.length() > 0,
                            rx.box(
                                rx.foreach(
                                    UIState.platform_bridge_inbound_logs,
                                    lambda row: rx.box(
                                        rx.vstack(
                                            rx.hstack(
                                                rx.text(row["timestamp"], class_name="dc-id"),
                                                rx.text(row["status"], class_name="dc-badge"),
                                                rx.text(row["client"], class_name="dc-muted"),
                                                spacing="2",
                                                wrap="wrap",
                                                width="100%",
                                                class_name="dc-inbound-log-meta",
                                            ),
                                            rx.text(row["url"], class_name="dc-inbound-log-url"),
                                            rx.text(
                                                UIState.i18n["platform_bridge.qq_inbound_query_label"],
                                                class_name="dc-field-label",
                                            ),
                                            rx.code_block(
                                                row["query"],
                                                language="json",
                                                can_copy=True,
                                                class_name="dc-codeblock dc-inbound-log-code",
                                            ),
                                            rx.text(
                                                UIState.i18n["platform_bridge.qq_inbound_headers_label"],
                                                class_name="dc-field-label",
                                            ),
                                            rx.code_block(
                                                row["headers"],
                                                language="json",
                                                can_copy=True,
                                                class_name="dc-codeblock dc-inbound-log-code",
                                            ),
                                            rx.text(
                                                UIState.i18n["platform_bridge.qq_inbound_request_label"],
                                                class_name="dc-field-label",
                                            ),
                                            rx.code_block(
                                                row["request_body"],
                                                language="json",
                                                can_copy=True,
                                                class_name="dc-codeblock dc-inbound-log-code",
                                            ),
                                            rx.text(
                                                UIState.i18n["platform_bridge.qq_inbound_response_label"],
                                                class_name="dc-field-label",
                                            ),
                                            rx.code_block(
                                                row["response_body"],
                                                language="json",
                                                can_copy=True,
                                                class_name="dc-codeblock dc-inbound-log-code",
                                            ),
                                            spacing="2",
                                            width="100%",
                                            align="start",
                                        ),
                                        class_name="dc-sub-card dc-inbound-log-card",
                                    ),
                                ),
                                class_name="dc-extension-detail-scroll dc-scroll-host dc-inbound-log-scroll",
                            ),
                            rx.text(UIState.i18n["platform_bridge.qq_inbound_logs_empty"], class_name="dc-muted"),
                        ),
                        rx.text(UIState.i18n["platform_bridge.qq_inbound_debug_hint"], class_name="dc-muted"),
                    ),
                    spacing="2",
                    width="100%",
                    align="start",
                    class_name="dc-sub-card",
                ),
                rx.box(
                    rx.text(UIState.i18n["platform_bridge.tutorial_title"], class_name="dc-row-title"),
                    rx.text(UIState.i18n["platform_bridge.tutorial_step_1"], class_name="dc-muted"),
                    rx.text(UIState.i18n["platform_bridge.tutorial_step_2"], class_name="dc-muted"),
                    rx.text(UIState.i18n["platform_bridge.tutorial_step_3"], class_name="dc-muted"),
                    rx.text(UIState.i18n["platform_bridge.tutorial_step_4"], class_name="dc-muted"),
                    spacing="1",
                    width="100%",
                    align="start",
                    class_name="dc-sub-card dc-bridge-tutorial",
                ),
                spacing="2",
                width="100%",
                align="start",
            ),
            class_name="dc-bridge-card",
        ),
        width="100%",
        spacing="4",
    )


def extensions_skills_tab() -> rx.Component:
    return rx.vstack(
        _card(
            "Skills",
            rx.vstack(
                rx.hstack(
                    rx.input(
                        placeholder="搜索已安装 Skills",
                        value=UIState.skill_search_query,
                        on_change=UIState.set_skill_search_query,
                        class_name="dc-input dc-extension-search-input",
                    ),
                    rx.select(
                        ["按安装时间", "按名称"],
                        value=UIState.skill_sort_label,
                        on_change=UIState.set_skill_sort_by,
                        class_name="dc-chat-plan-select",
                    ),
                    rx.select(
                        ["8", "12", "20"],
                        value=UIState.skill_page_size,
                        on_change=UIState.set_skill_page_size,
                        class_name="dc-chat-plan-select",
                    ),
                    rx.text(f"共 {UIState.skills.length()} 个", class_name="dc-muted"),
                    spacing="2",
                    width="100%",
                    align="center",
                ),
                rx.hstack(
                    rx.text(UIState.skill_page_label, class_name="dc-muted"),
                    rx.button("上一页", on_click=UIState.previous_skill_page, class_name="dc-small-btn"),
                    rx.button("下一页", on_click=UIState.next_skill_page, class_name="dc-small-btn"),
                    class_name="dc-pagination-row",
                ),
                rx.cond(
                    UIState.paginated_skill_rows.length() > 0,
                    rx.box(
                        rx.foreach(
                            UIState.paginated_skill_rows,
                            lambda row: rx.box(
                                rx.vstack(
                                    rx.hstack(
                                        rx.vstack(
                                            rx.text(row["name"], class_name="dc-row-title"),
                                            rx.text(row["description"], class_name="dc-muted"),
                                            rx.hstack(
                                                rx.text(row["tags"], class_name="dc-id"),
                                                rx.cond(
                                                    row["installed_at"] != "",
                                                    rx.text(f"安装于 {row['installed_at']}", class_name="dc-muted"),
                                                    rx.fragment(),
                                                ),
                                                spacing="2",
                                                wrap="wrap",
                                            ),
                                            align="start",
                                            spacing="1",
                                        ),
                                        rx.button(
                                            rx.cond(row["enabled"] == "enabled", "已开启", "已关闭"),
                                            on_click=lambda: UIState.toggle_skill_enabled(row["path"]),
                                            class_name=rx.cond(
                                                row["enabled"] == "enabled",
                                                "dc-small-btn dc-extension-switch is-on",
                                                "dc-small-btn dc-extension-switch is-off",
                                            ),
                                        ),
                                        justify="between",
                                        width="100%",
                                        align="start",
                                    ),
                                    rx.hstack(
                                        rx.text(row["path"], class_name="dc-id"),
                                        rx.spacer(),
                                        rx.button("查看详情", on_click=lambda: UIState.open_skill_detail(row["path"]), class_name="dc-small-btn"),
                                        rx.button(
                                            "删除",
                                            on_click=lambda: UIState.request_delete_skill(row["path"], row["name"]),
                                            class_name="dc-small-btn dc-task-group-delete",
                                        ),
                                        width="100%",
                                        align="center",
                                    ),
                                    width="100%",
                                    spacing="2",
                                    align="start",
                                ),
                                class_name="dc-list-item dc-extension-card",
                            ),
                        ),
                        class_name="dc-extension-card-grid",
                    ),
                    rx.text("暂无已安装 Skills", class_name="dc-muted"),
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
        ),
        _card(
            "Skills 配置教程",
            rx.vstack(
                rx.text("1. 将 Skill markdown 文件放到本地目录，系统会自动发现。", class_name="dc-muted"),
                rx.text("2. 对每个 Skill 通过卡片开关控制是否暴露给模型。", class_name="dc-muted"),
                rx.text("3. 开启后模型可通过 skill_registry 使用；关闭后不会暴露。", class_name="dc-muted"),
                rx.box(
                    rx.text("ClawHub 智能安装（推荐）", class_name="dc-row-title"),
                    rx.text("先 Dry Run 预览匹配结果，再确认安装，可减少误装。", class_name="dc-muted"),
                    rx.text(
                        "支持关键词搜索，也支持直接输入 slug:agent-browser 或 ClawHub 技能详情页链接后安装。",
                        class_name="dc-muted",
                    ),
                    rx.hstack(
                        rx.input(
                            placeholder="输入关键词、slug:agent-browser 或详情页链接",
                            value=UIState.clawhub_query,
                            on_change=UIState.set_clawhub_query,
                            class_name="dc-input",
                        ),
                        rx.input(
                            placeholder="https://clawhub.ai",
                            value=UIState.clawhub_source_url,
                            on_change=UIState.set_clawhub_source_url,
                            class_name="dc-input",
                        ),
                        spacing="2",
                        width="100%",
                    ),
                    rx.hstack(
                        rx.button(
                            "预览",
                            on_click=lambda: UIState.run_clawhub_auto_install(True),
                            class_name="dc-small-btn",
                        ),
                        rx.button(
                            "确认安装",
                            on_click=lambda: UIState.run_clawhub_auto_install(False),
                            class_name="dc-cta",
                        ),
                        spacing="2",
                        class_name="dc-clawhub-actions",
                    ),
                    rx.cond(
                        UIState.clawhub_panel_hint != "",
                        rx.box(
                            rx.text("搜索受限时的替代方案", class_name="dc-clawhub-tip-title"),
                            rx.text(UIState.clawhub_panel_hint, class_name="dc-clawhub-tip-text"),
                            class_name="dc-clawhub-tip dc-clawhub-tip-warning",
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        UIState.clawhub_selected_slug != "",
                        rx.box(
                            rx.hstack(
                                rx.vstack(
                                    rx.text(UIState.clawhub_preview_name, class_name="dc-row-title"),
                                    rx.text(UIState.clawhub_preview_summary, class_name="dc-muted"),
                                    rx.text(UIState.clawhub_selected_slug, class_name="dc-id"),
                                    align="start",
                                    spacing="1",
                                ),
                                rx.cond(
                                    UIState.clawhub_preview_score != "",
                                    rx.text(
                                        f"Score {UIState.clawhub_preview_score}",
                                        class_name="dc-kpi-pill dc-kpi-positive",
                                    ),
                                    rx.text("Ready", class_name="dc-kpi-pill dc-kpi-neutral"),
                                ),
                                justify="between",
                                width="100%",
                                align="start",
                            ),
                            rx.hstack(
                                rx.box(
                                    rx.text("版本", class_name="dc-muted"),
                                    rx.text(UIState.clawhub_preview_version, class_name="dc-row-title"),
                                    class_name="dc-list-item",
                                    width="100%",
                                ),
                                rx.box(
                                    rx.text("候选数", class_name="dc-muted"),
                                    rx.text(
                                        rx.cond(
                                            UIState.clawhub_preview_candidate_count != "",
                                            UIState.clawhub_preview_candidate_count,
                                            "1",
                                        ),
                                        class_name="dc-row-title",
                                    ),
                                    class_name="dc-list-item",
                                    width="100%",
                                ),
                                rx.box(
                                    rx.text("查询词", class_name="dc-muted"),
                                    rx.text(
                                        rx.cond(
                                            UIState.clawhub_preview_query != "",
                                            UIState.clawhub_preview_query,
                                            "直接使用已选 Skill",
                                        ),
                                        class_name="dc-row-title",
                                    ),
                                    class_name="dc-list-item",
                                    width="100%",
                                ),
                                spacing="2",
                                width="100%",
                                align="stretch",
                            ),
                            rx.box(
                                rx.text("来源", class_name="dc-muted"),
                                rx.text(UIState.clawhub_source_url, class_name="dc-id", white_space="pre-wrap"),
                                class_name="dc-list-item",
                                width="100%",
                            ),
                            rx.cond(
                                UIState.clawhub_preview_package_name != "",
                                rx.hstack(
                                    rx.box(
                                        rx.text("已安装包", class_name="dc-muted"),
                                        rx.text(UIState.clawhub_preview_package_name, class_name="dc-row-title"),
                                        class_name="dc-list-item",
                                        width="100%",
                                    ),
                                    rx.box(
                                        rx.text("安装目录", class_name="dc-muted"),
                                        rx.text(
                                            UIState.clawhub_preview_install_dir,
                                            class_name="dc-id",
                                            white_space="pre-wrap",
                                        ),
                                        class_name="dc-list-item",
                                        width="100%",
                                    ),
                                    spacing="2",
                                    width="100%",
                                    align="stretch",
                                ),
                                rx.fragment(),
                            ),
                            spacing="2",
                            width="100%",
                            align="start",
                        ),
                        rx.fragment(),
                    ),
                    spacing="2",
                    width="100%",
                    align="start",
                    class_name="dc-sub-card dc-clawhub-panel",
                ),
                rx.text(f"本地 Skills 路径: {UIState.skill_install_path}", class_name="dc-code", white_space="pre-wrap"),
                rx.upload(
                    rx.vstack(
                        rx.button("选择 Skills 压缩包", class_name="dc-small-btn"),
                        rx.text("拖放 zip 文件到这里，或点击按钮选择文件，可多选。", class_name="dc-muted"),
                        rx.text("压缩包内必须包含 SKILL.md / SKILL.MD，否则不会安装。", class_name="dc-muted"),
                        spacing="2",
                        align="center",
                    ),
                    id="skill_package_upload",
                    multiple=True,
                    max_files=24,
                    accept={
                        "application/zip": [".zip"],
                        "application/x-zip-compressed": [".zip"],
                    },
                    on_drop=UIState.handle_skill_archive_upload(rx.upload_files(upload_id="skill_package_upload")),
                    class_name="dc-skill-upload",
                ),
                rx.box(
                    rx.foreach(
                        rx.selected_files("skill_package_upload"),
                        lambda filename: rx.text(filename, class_name="dc-id"),
                    ),
                    class_name="dc-upload-file-list",
                ),
                rx.hstack(
                    rx.button(
                        "安装已选压缩包",
                        on_click=UIState.handle_skill_archive_upload(rx.upload_files(upload_id="skill_package_upload")),
                        class_name="dc-cta",
                    ),
                    rx.button("清空已选", on_click=rx.clear_selected_files("skill_package_upload"), class_name="dc-small-btn"),
                    spacing="2",
                ),
                rx.box(
                    rx.foreach(
                        UIState.skill_resource_sites,
                        lambda site: rx.box(
                            rx.hstack(
                                rx.vstack(
                                    rx.text(site["name"], class_name="dc-row-title"),
                                    rx.text(site["description"], class_name="dc-muted"),
                                    rx.text(site["url"], class_name="dc-id"),
                                    align="start",
                                    spacing="1",
                                ),
                                rx.hstack(
                                    rx.link("访问网站", href=site["url"], is_external=True, class_name="dc-small-btn"),
                                    rx.button("尝试安装", on_click=lambda: UIState.install_skills_from_source(site["url"]), class_name="dc-small-btn"),
                                    spacing="2",
                                ),
                                justify="between",
                                width="100%",
                            ),
                            class_name="dc-list-item",
                        ),
                    ),
                    class_name="dc-extension-resource-list",
                ),
                spacing="2",
                width="100%",
                align="start",
            ),
        ),
        width="100%",
        spacing="4",
    )


def extensions_mcp_tab() -> rx.Component:
    return rx.vstack(
        _card(
            "MCP 服务",
            rx.vstack(
                rx.hstack(
                    rx.input(
                        placeholder="搜索已安装 MCP 服务",
                        value=UIState.mcp_search_query,
                        on_change=UIState.set_mcp_search_query,
                        class_name="dc-input dc-extension-search-input",
                    ),
                    rx.text(f"共 {UIState.mcp_servers.length()} 个", class_name="dc-muted"),
                    spacing="2",
                    width="100%",
                    align="center",
                ),
                rx.cond(
                    UIState.filtered_mcp_rows.length() > 0,
                    rx.box(
                        rx.foreach(
                            UIState.filtered_mcp_rows,
                            lambda row: rx.box(
                                rx.vstack(
                                    rx.hstack(
                                        rx.vstack(
                                            rx.text(row["name"], class_name="dc-row-title"),
                                            rx.text(row["description"], class_name="dc-muted"),
                                            rx.text(f"{row['transport']} · {row['command']}", class_name="dc-id"),
                                            align="start",
                                            spacing="1",
                                        ),
                                        rx.button(
                                            rx.cond(row["enabled"] == "enabled", "已开启", "已关闭"),
                                            on_click=lambda: UIState.toggle_mcp_enabled(row["name"]),
                                            class_name=rx.cond(
                                                row["enabled"] == "enabled",
                                                "dc-small-btn dc-extension-switch is-on",
                                                "dc-small-btn dc-extension-switch is-off",
                                            ),
                                        ),
                                        justify="between",
                                        width="100%",
                                        align="start",
                                    ),
                                    rx.hstack(
                                        rx.text(rx.cond(row["args"] != "", row["args"], "无参数"), class_name="dc-id"),
                                        rx.spacer(),
                                        rx.button("查看详情", on_click=lambda: UIState.open_mcp_detail(row["name"]), class_name="dc-small-btn"),
                                        width="100%",
                                        align="center",
                                    ),
                                    width="100%",
                                    spacing="2",
                                    align="start",
                                ),
                                class_name=rx.cond(
                                    UIState.selected_mcp_name == row["name"],
                                    "dc-list-item dc-extension-card is-selected",
                                    "dc-list-item dc-extension-card",
                                ),
                            ),
                        ),
                        class_name="dc-extension-card-grid",
                    ),
                    rx.text("暂无已安装 MCP 服务", class_name="dc-muted"),
                ),
                spacing="3",
                width="100%",
                align="start",
            ),
        ),
        _card(
            "MCP 配置教程",
            rx.vstack(
                rx.text("1. 配置 MCP 名称、传输协议和命令或 Endpoint。", class_name="dc-muted"),
                rx.text("2. 通过卡片开关控制是否暴露给模型。", class_name="dc-muted"),
                rx.text("3. 仅开启状态的 MCP 会被模型发现并调用。", class_name="dc-muted"),
                rx.hstack(
                    rx.input(placeholder="名称", value=UIState.mcp_name, on_change=UIState.set_mcp_name, class_name="dc-input"),
                    rx.select(["stdio", "http", "sse"], value=UIState.mcp_transport, on_change=UIState.set_mcp_transport),
                    spacing="2",
                    width="100%",
                ),
                rx.input(placeholder="命令或 Endpoint", value=UIState.mcp_command, on_change=UIState.set_mcp_command, class_name="dc-input"),
                rx.input(placeholder="参数(逗号分隔)", value=UIState.mcp_args, on_change=UIState.set_mcp_args, class_name="dc-input"),
                rx.input(placeholder="描述", value=UIState.mcp_description, on_change=UIState.set_mcp_description, class_name="dc-input"),
                rx.text(
                    UIState.mcp_form_hint,
                    class_name=rx.cond(UIState.mcp_form_valid, "dc-form-hint-ok", "dc-form-hint"),
                ),
                rx.button("保存 MCP", on_click=UIState.upsert_mcp, class_name="dc-cta"),
                rx.box(
                    rx.foreach(
                        UIState.mcp_resource_sites,
                        lambda site: rx.box(
                            rx.hstack(
                                rx.vstack(
                                    rx.text(site["name"], class_name="dc-row-title"),
                                    rx.text(site["description"], class_name="dc-muted"),
                                    rx.text(site["url"], class_name="dc-id"),
                                    align="start",
                                    spacing="1",
                                ),
                                rx.hstack(
                                    rx.link("访问网站", href=site["url"], is_external=True, class_name="dc-small-btn"),
                                    rx.button("尝试安装", on_click=lambda: UIState.install_mcp_from_source(site["url"]), class_name="dc-small-btn"),
                                    spacing="2",
                                ),
                                justify="between",
                                width="100%",
                            ),
                            class_name="dc-list-item",
                        ),
                    ),
                    class_name="dc-extension-resource-list",
                ),
                spacing="2",
                width="100%",
                align="start",
            ),
        ),
        width="100%",
        spacing="4",
    )


def _extension_detail_modal() -> rx.Component:
    return rx.cond(
        UIState.extension_detail_kind != "",
        rx.box(
            rx.box(class_name="dc-session-modal-backdrop", on_click=UIState.close_extension_detail),
            rx.box(
                rx.cond(
                    UIState.extension_detail_kind == "skill",
                    rx.vstack(
                        rx.hstack(
                            rx.text(UIState.selected_skill_detail["name"], class_name="dc-session-modal-title"),
                            rx.button("关闭", on_click=UIState.close_extension_detail, class_name="dc-small-btn"),
                            justify="between",
                            width="100%",
                            align="center",
                        ),
                        rx.text(UIState.selected_skill_detail["description"], class_name="dc-muted"),
                        rx.text(f"状态: {UIState.selected_skill_detail['enabled']}", class_name="dc-muted"),
                        rx.text(f"标签: {UIState.selected_skill_detail['tags']}", class_name="dc-muted"),
                        rx.text(UIState.selected_skill_detail["path"], class_name="dc-code", white_space="pre-wrap"),
                        rx.box(
                            rx.cond(
                                UIState.selected_skill_markdown != "",
                                rx.markdown(UIState.selected_skill_markdown, class_name="dc-markdown dc-skill-detail-markdown"),
                                rx.text("未找到可渲染的 Skill Markdown。", class_name="dc-muted"),
                            ),
                            class_name="dc-extension-detail-scroll dc-scroll-host",
                        ),
                        spacing="3",
                        width="100%",
                        align="start",
                    ),
                    rx.vstack(
                        rx.hstack(
                            rx.text(UIState.selected_mcp_detail["name"], class_name="dc-session-modal-title"),
                            rx.button("关闭", on_click=UIState.close_extension_detail, class_name="dc-small-btn"),
                            justify="between",
                            width="100%",
                            align="center",
                        ),
                        rx.text(UIState.selected_mcp_detail["description"], class_name="dc-muted"),
                        rx.text(f"状态: {UIState.selected_mcp_detail['enabled']}", class_name="dc-muted"),
                        rx.text(f"传输: {UIState.selected_mcp_detail['transport']}", class_name="dc-muted"),
                        rx.text(f"命令: {UIState.selected_mcp_detail['command']}", class_name="dc-code", white_space="pre-wrap"),
                        rx.text(f"参数: {UIState.selected_mcp_detail['args']}", class_name="dc-id", white_space="pre-wrap"),
                        rx.cond(
                            UIState.selected_mcp_detail["env"] != "",
                            rx.text(f"环境变量: {UIState.selected_mcp_detail['env']}", class_name="dc-code", white_space="pre-wrap"),
                            rx.fragment(),
                        ),
                        spacing="3",
                        width="100%",
                        align="start",
                    ),
                ),
                class_name="dc-session-modal-card dc-extension-modal-card",
            ),
            class_name="dc-session-modal-layer",
        ),
        rx.fragment(),
    )


def extensions_page() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.button(
                "Skills",
                on_click=lambda: UIState.set_extension_tab("skills"),
                class_name=rx.cond(UIState.extension_tab == "skills", "dc-cta", "dc-small-btn"),
            ),
            rx.button(
                "MCP",
                on_click=lambda: UIState.set_extension_tab("mcp"),
                class_name=rx.cond(UIState.extension_tab == "mcp", "dc-cta", "dc-small-btn"),
            ),
            spacing="2",
        ),
        rx.cond(
            UIState.extension_tab == "mcp",
            extensions_mcp_tab(),
            extensions_skills_tab(),
        ),
        _extension_detail_modal(),
        _skill_delete_modal(),
        width="100%",
        spacing="4",
    )


def _model_field(label: str, control: rx.Component, hint: rx.Component | None = None) -> rx.Component:
    return rx.vstack(
        rx.text(label, class_name="dc-model-field-label"),
        control,
        hint if hint is not None else rx.fragment(),
        align="start",
        spacing="1",
        width="100%",
        class_name="dc-model-field",
    )


def _model_profile_chip(row: dict[str, str]) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.box(
                rx.text(row.get("name", ""), class_name="dc-model-profile-chip-name"),
                rx.text(f"{row.get('llm_provider', '')} · {row.get('llm_model', '')}", class_name="dc-model-profile-chip-meta"),
                class_name="dc-model-profile-chip-main",
            ),
            rx.hstack(
                rx.button("切换", on_click=lambda: UIState.switch_model_profile(row["id"]), class_name="dc-small-btn"),
                rx.button("删除", on_click=lambda: UIState.delete_model_profile(row["id"]), class_name="dc-small-btn"),
                spacing="2",
            ),
            justify="between",
            align="center",
            width="100%",
        ),
        class_name=rx.cond(
            UIState.active_model_profile_id == row["id"],
            "dc-model-profile-chip is-active",
            "dc-model-profile-chip",
        ),
    )


def model_page() -> rx.Component:
    return rx.vstack(
        _card(
            "配置档案",
            rx.vstack(
                rx.text("支持为不同任务保存多套模型参数，并在聊天页面快速切换。", class_name="dc-model-field-hint"),
                _model_field(
                    "配置名称",
                    rx.input(
                        placeholder="例如：代码评审 / 写作 / 调试",
                        value=UIState.model_profile_name,
                        on_change=UIState.set_model_profile_name,
                        class_name="dc-input",
                    ),
                ),
                rx.hstack(
                    rx.button("保存并应用", on_click=UIState.save_model_settings, class_name="dc-cta"),
                    rx.button("另存为新配置", on_click=UIState.save_as_new_model_profile, class_name="dc-small-btn"),
                    rx.button(
                        "删除当前配置",
                        on_click=lambda: UIState.delete_model_profile(UIState.active_model_profile_id),
                        class_name="dc-small-btn",
                    ),
                    spacing="2",
                    class_name="dc-model-actions",
                ),
                rx.box(
                    rx.cond(
                        UIState.model_profiles.length() > 0,
                        rx.foreach(UIState.model_profiles, _model_profile_chip),
                        rx.text("暂无配置，请先保存当前参数。", class_name="dc-muted"),
                    ),
                    class_name="dc-model-profile-list",
                ),
                align="start",
                spacing="3",
                width="100%",
                class_name="dc-model-profile-panel",
            ),
        ),
        _card(
            "运行时配置",
            rx.vstack(
                rx.box(
                    _model_field(
                        "模型提供商",
                        rx.select(
                            UIState.model_provider_options,
                            value=UIState.llm_provider,
                            on_change=UIState.set_llm_provider,
                            class_name="dc-chat-model-select",
                        ),
                        rx.text("已支持 OpenAI / Ollama / Google Gemini / GitHub Copilot / Mock", class_name="dc-model-field-hint"),
                    ),
                    _model_field(
                        "模型名称",
                        rx.input(
                            placeholder="例如：gpt-4o-mini / gemini-2.0-flash",
                            value=UIState.llm_model,
                            on_change=UIState.set_llm_model,
                            class_name="dc-input",
                        ),
                    ),
                    _model_field(
                        "Base URL",
                        rx.input(
                            placeholder="留空将使用默认兼容地址",
                            value=UIState.llm_base_url,
                            on_change=UIState.set_llm_base_url,
                            class_name="dc-input",
                        ),
                        rx.text(UIState.model_base_url_hint, class_name="dc-model-field-hint"),
                    ),
                    _model_field(
                        "Temperature",
                        rx.vstack(
                            rx.hstack(
                                rx.input(
                                    type="range",
                                    min="0",
                                    max="2",
                                    step="0.1",
                                    value=UIState.llm_temperature,
                                    on_change=UIState.set_llm_temperature,
                                    class_name="dc-model-slider",
                                ),
                                rx.text(UIState.llm_temperature, class_name="dc-model-slider-value"),
                                width="100%",
                                align="center",
                                spacing="3",
                            ),
                            rx.hstack(
                                rx.text("0.0", class_name="dc-model-slider-marks"),
                                rx.text("1.0", class_name="dc-model-slider-marks"),
                                rx.text("2.0", class_name="dc-model-slider-marks"),
                                justify="between",
                                width="100%",
                            ),
                            spacing="1",
                            width="100%",
                            align="start",
                            class_name="dc-model-slider-wrap",
                        ),
                        rx.text("数值越高回答越发散，推荐区间 0.2 - 1.0", class_name="dc-model-field-hint"),
                    ),
                    _model_field(
                        "Max Tokens",
                        rx.input(
                            type="number",
                            placeholder="最大输出 Tokens",
                            value=UIState.llm_max_tokens,
                            on_change=UIState.set_llm_max_tokens,
                            class_name="dc-input",
                        ),
                    ),
                    _model_field(
                        "Thinking 模式",
                        rx.select(
                            ["enabled", "disabled"],
                            value=UIState.llm_enable_thinking,
                            on_change=UIState.set_llm_enable_thinking,
                            class_name="dc-chat-model-select",
                        ),
                        rx.text("仅对支持该参数的模型生效，部分 Qwen 非流式接口会强制关闭。", class_name="dc-model-field-hint"),
                    ),
                    _model_field(
                        "心跳机制",
                        rx.select(
                            ["enabled", "disabled"],
                            value=UIState.heartbeat_enabled,
                            on_change=UIState.set_heartbeat_enabled,
                            class_name="dc-chat-model-select",
                        ),
                        rx.text("关闭后将停用聊天/任务的心跳动画刷新，降低页面抖动。", class_name="dc-model-field-hint"),
                    ),
                    _model_field(
                        "API Key",
                        rx.input(
                            type="password",
                            placeholder="输入对应提供商密钥",
                            value=UIState.llm_api_key,
                            on_change=UIState.set_llm_api_key,
                            class_name="dc-input",
                        ),
                    ),
                    _model_field(
                        "API Key 持久化",
                        rx.select(
                            ["enabled", "disabled"],
                            value=UIState.persist_api_key,
                            on_change=UIState.set_persist_api_key,
                            class_name="dc-chat-model-select",
                        ),
                    ),
                    class_name="dc-model-grid",
                ),
                rx.hstack(
                    rx.button("保存运行开关", on_click=UIState.save_runtime_switches, class_name="dc-small-btn"),
                    rx.button("验证客户端", on_click=UIState.validate_model_client, class_name="dc-small-btn"),
                    rx.button("恢复默认", on_click=UIState.reset_model_settings, class_name="dc-small-btn"),
                    spacing="2",
                    class_name="dc-model-actions",
                ),
                align="start",
                spacing="3",
                width="100%",
                class_name="dc-model-form",
            ),
        ),
        _card("环境变量预览", rx.text(UIState.model_env_preview, class_name="dc-code", white_space="pre-wrap")),
        width="100%",
        spacing="4",
        class_name="dc-model-layout",
    )


def governance_page() -> rx.Component:
    return rx.vstack(
        rx.box(
            _card(
                "策略规则",
                rx.vstack(
                    rx.cond(
                        UIState.policy_rules.length() > 0,
                        rx.foreach(
                            UIState.policy_rules,
                            lambda row: rx.box(
                                rx.hstack(
                                    rx.text(row["name"], class_name="dc-row-title"),
                                    rx.button("删除", on_click=lambda: UIState.remove_policy_rule(row["id"]), class_name="dc-small-btn"),
                                    justify="between",
                                    width="100%",
                                ),
                                rx.text(
                                    f"{row['scope']} · {row['target']} · {row['decision']}",
                                    class_name="dc-muted",
                                ),
                                class_name="dc-list-item",
                            ),
                        ),
                        rx.text("暂无策略规则", class_name="dc-muted"),
                    ),
                    rx.input(placeholder="规则名称", value=UIState.policy_name, on_change=UIState.set_policy_name, class_name="dc-input"),
                    rx.input(placeholder="Scope", value=UIState.policy_scope, on_change=UIState.set_policy_scope, class_name="dc-input"),
                    rx.input(placeholder="Target", value=UIState.policy_target, on_change=UIState.set_policy_target, class_name="dc-input"),
                    rx.select(["allow", "ask", "deny"], value=UIState.policy_decision, on_change=UIState.set_policy_decision),
                    rx.input(placeholder="描述", value=UIState.policy_description, on_change=UIState.set_policy_description, class_name="dc-input"),
                    rx.select(["enabled", "disabled"], value=UIState.policy_enabled, on_change=UIState.set_policy_enabled),
                    rx.button("创建策略", on_click=UIState.create_policy_rule, class_name="dc-cta"),
                    align="start",
                    spacing="2",
                ),
            ),
            _card(
                "审计时间线",
                rx.vstack(
                    rx.hstack(
                        rx.input(type="number", value=UIState.audit_limit, on_change=UIState.set_audit_limit, class_name="dc-input"),
                        rx.input(
                            placeholder="筛选事件 / 资源 / 元数据",
                            value=UIState.audit_query,
                            on_change=UIState.set_audit_query,
                            class_name="dc-input",
                        ),
                        rx.select(
                            ["all", "ok", "warn", "error"],
                            value=UIState.audit_status_filter,
                            on_change=UIState.set_audit_status_filter,
                        ),
                        rx.button("刷新审计", on_click=UIState.refresh_audit, class_name="dc-small-btn"),
                        spacing="2",
                    ),
                    rx.text(
                        rx.cond(UIState.filtered_audit_events.length() > 0, "", "筛选后无记录"),
                        class_name="dc-muted",
                    ),
                    rx.cond(
                        UIState.filtered_audit_events.length() > 0,
                        rx.hstack(
                            rx.text("筛选后", class_name="dc-muted"),
                            rx.text(UIState.filtered_audit_events.length(), class_name="dc-muted"),
                            rx.text("条记录", class_name="dc-muted"),
                            spacing="1",
                        ),
                        rx.fragment(),
                    ),
                    rx.cond(
                        UIState.filtered_audit_events.length() > 0,
                        rx.foreach(
                            UIState.filtered_audit_events,
                            lambda row: rx.box(
                                rx.hstack(
                                    rx.text(row["event"], class_name="dc-row-title"),
                                    rx.text(row["status"], class_name="dc-badge"),
                                    spacing="2",
                                ),
                                rx.text(f"{row['timestamp']} · {row['actor']} · {row['resource']}", class_name="dc-muted"),
                                rx.text(row["metadata"], class_name="dc-code", white_space="pre-wrap"),
                                class_name="dc-list-item",
                            ),
                        ),
                        rx.text("暂无审计事件", class_name="dc-muted"),
                    ),
                    align="start",
                    spacing="2",
                ),
            ),
            class_name="dc-two-col",
        ),
        width="100%",
        spacing="4",
    )


def about_page() -> rx.Component:
    return rx.vstack(
        rx.markdown(UIState.about_markdown, class_name="dc-markdown"),
        width="100%",
        spacing="4",
    )


def content_router() -> rx.Component:
    return rx.cond(
        UIState.selected_page == "dashboard",
        dashboard_page(),
        rx.cond(
            UIState.selected_page == "chat",
            chat_page(),
            rx.cond(
                UIState.selected_page == "task_center",
                task_page(),
                rx.cond(
                    UIState.selected_page == "artifact_center",
                    artifact_page(),
                    rx.cond(
                        UIState.selected_page == "platform_bridge",
                        platform_bridge_page(),
                        rx.cond(
                            UIState.selected_page == "extensions",
                            extensions_page(),
                            rx.cond(
                                UIState.selected_page == "model_studio",
                                model_page(),
                                rx.cond(
                                    UIState.selected_page == "governance",
                                    governance_page(),
                                    about_page(),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def index() -> rx.Component:
    return rx.box(
        rx.hstack(
            sidebar(),
            rx.box(
                _page_header(),
                rx.box(content_router(), class_name="dc-page-switch"),
                class_name="dc-main",
            ),
            class_name=rx.cond(UIState.sidebar_collapsed, "dc-shell dc-shell-collapsed", "dc-shell"),
        ),
        class_name="dc-root",
    )


app = rx.App(
    stylesheets=[
        "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap",
        "/deepcode_reflex.css",
    ]
)
app.add_page(index, title="DeepCode Reflex Console", on_load=UIState.bootstrap)



