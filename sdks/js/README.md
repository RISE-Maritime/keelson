# keelson-sdk (javascript)

A javascript SDK for [keelson](https://github.com/MO-RISE/keelson).

## Basic usage
See the [tests](https://github.com/MO-RISE/keelson/blob/main/sdks/js/keelson/index.test.ts)


## Example 

```javascript

// Get reqest to zenoh node
axios.get("zenoh_router_url+keyexpression").then(res => {
             res.data
        })
```