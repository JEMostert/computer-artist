#include <wayland-client.h>
#include "fake-input-client.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
static struct org_kde_kwin_fake_input *input;
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    if (!strcmp(interface,"org_kde_kwin_fake_input")) input=wl_registry_bind(registry,name,&org_kde_kwin_fake_input_interface,version<4?version:4);
}
static void removed(void *data,struct wl_registry *registry,uint32_t name){}
int main(int argc,char **argv) {
    if(argc<3 || !strstr(argv[1],"/ca-stock-") || !strstr(argv[1],"/wayland-test")) {fprintf(stderr,"Only a ca-stock test display is allowed\n");return 2;}
    struct wl_display *display=wl_display_connect(argv[1]); if(!display)return 1;
    struct wl_registry *registry=wl_display_get_registry(display);
    const struct wl_registry_listener listener={global,removed};
    wl_registry_add_listener(registry,&listener,NULL);wl_display_roundtrip(display);
    if(!input)return 1;
    org_kde_kwin_fake_input_authenticate(input,"Computer Artist test","Stock-plugin independent input regression");
    wl_display_roundtrip(display);usleep(100000);wl_display_roundtrip(display);
    if(!strcmp(argv[2],"move") && argc==5) org_kde_kwin_fake_input_pointer_motion_absolute(input,wl_fixed_from_double(atof(argv[3])),wl_fixed_from_double(atof(argv[4])));
    else if(!strcmp(argv[2],"click")) {org_kde_kwin_fake_input_button(input,272,1);org_kde_kwin_fake_input_button(input,272,0);}
    else if(!strcmp(argv[2],"key") && argc==4) {org_kde_kwin_fake_input_keyboard_key(input,atoi(argv[3]),1);org_kde_kwin_fake_input_keyboard_key(input,atoi(argv[3]),0);}
    else if(!strcmp(argv[2],"hold")) { while(wl_display_dispatch(display)>=0) {} return 0; }
    else return 2;
    wl_display_roundtrip(display);wl_display_disconnect(display);return 0;
}
